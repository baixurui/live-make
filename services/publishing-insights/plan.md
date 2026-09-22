# 发布与指标技术设计

用户已授权按规格推进实现；下述实现选择随代码一并交付审查。

## 架构与技术选择

- Python 3.11 + FastAPI 提供内部发布入口、回执、可靠事件拉取/确认及指标接口。
- SQLite 使用独立持久卷、唯一约束和 BEGIN IMMEDIATE 事务实现本期单机持久化。多进程共享同一数据库文件可串行写入；不支持跨主机共享文件。后续高吞吐部署可替换为 PostgreSQL，不冒充现有 Compose PostgreSQL 集成。
- 业务记录通过 TaskSource.get(task_id) 获取，生产配置使用带服务凭据的 HTTP 查询。受控测试替身不进入生产默认配置。
- Publisher.publish(key, attempt, seed) 返回统一 PublishResult。模拟器无外部副作用，SHA-256 稳定散列决定结果。每个事务内完成一次调用和结果落盘；崩溃回滚后可重新计算，逻辑模拟作品标识不变。真实适配器必须支持请求幂等/查询对账，不能直接把外部网络副作用放入此保证。
- 一个逻辑请求保存事件、业务上下文、固定种子、首次受理时刻和尝试次数。幂等键、任务、回执、账号/排期日均有唯一约束。处理终态请求无需重新读取后来变化的审批记录。
- 每次尝试前校验可信审批和业务上下文。第一次失败提交后才进行第二次尝试；依赖暂不可用保留处理中等待恢复，资格撤销则终止。成功或最终失败与 outbox 同事务落盘。
- 模块只发最终 task.published.v1 / task.failed.v1。首次模拟失败保存在尝试记录中，不向工作流发可触发外部重试的事件。最终失败 retryable=false；工作流按 failed_step=publishing 转 PAUSED。
- 工作流使用认证后的事件拉取/确认接口。只有应用状态更新后才能确认；未确认事件重复拉取保持同一 event_id。无需虚构尚不存在的工作流推送地址。
- 内置后台循环恢复处理中请求、补齐快照和清理过期数据。快照完成标记独立保留，避免清理后重复生成。

## 模块与文件

- publishing/models.py：请求、可信上下文、回执、事件、指标响应与时间校验。
- publishing/storage.py：数据库建表、连接和事务边界。
- publishing/adapters.py：模拟发布、指标生成和 HTTP 业务上下文读取。
- publishing/service.py：资格、唯一受理、两次尝试、outbox、恢复。
- publishing/insights.py：三个阶段快照、保留清理、滚动窗口与日期汇总。
- publishing/api.py：FastAPI 工厂、服务凭据、账号范围、生命周期和后台循环。
- publishing/__main__.py：环境配置启动。
- tests/：行为测试、持久化/并发/故障恢复、HTTP 授权和契约测试。

## 接口与协作

| 接口 | 调用方 | 语义 |
| --- | --- | --- |
| POST /api/v1/internal/publishing/requests | 工作流 | 消费 task.publish_requested.v1；返回回执，处理中返回 202 |
| GET /api/v1/internal/publishing/receipts/{receipt_id} | 工作流 | 回执查询 |
| GET /api/v1/internal/publishing/events | 工作流 | 拉取未确认最终事件，最多 100 条 |
| POST /api/v1/internal/publishing/events/{event_id}/ack | 工作流 | 状态更新落盘后幂等确认 |
| GET /api/v1/metrics?account_id=... | 业务网关 | 按服务端配置的账号范围读取指标；不接受浏览器自报账号权限 |
| GET /api/v1/internal/tasks/{task_id}/publishing-context | 业务 API（待接入） | 返回完整可信发布上下文，模块提供响应契约和真实 HTTP 客户端 |

原发布请求仍只要求 task_id、receipt_id、scheduled_at；可选 account_id、media_version 若提供必须与可信上下文匹配。重放请求按业务字段规范化比较，不将 envelope 的 event_id/occurred_at 当业务差异。

上下文包含 task_id、account_id、status、scheduled_at、media_version、approved_media_version、script_approved、video_approved、qc_passed、risk_level，以及可选 0–100 的 qc_score/review_score。审批通过且 HIGH 可以发布，BLOCKED 不可发布。允许 SCHEDULED/PUBLISHING，未排期状态不能仅凭审批字段发布。

所有入口使用独立角色凭据：工作流凭据与指标凭据不可相同；指标凭据映射具体账号集合，不允许由 query/header 扩大授权范围。业务 API 出站凭据单独配置；HTTP 默认不跟随重定向，读取故障按依赖不可用处理。

共享 OpenAPI 用外部引用接入本模块导出的接口定义，业务上下文定义作为提供方实现依据；共享事件补充 payload 字段类型，保留原必需字段，不增加破坏性的 required 字段。其他模块尚为空目录，真实生产方/消费方联调不可在本次伪称完成。

## 指标口径

- 快照阶段为 3600、86400、604800 秒；持久化阶段完成标记、计划时间、生成时间、非负累计指标。
- 固定评分均值（缺失项为 50）和稳定散列生成基础规模及互动比例，各阶段乘数为 1/3/7；评分影响分布但随机差异仍存在。
- 最近 168 小时按 [start, end) 选作品；最新可用快照采样时间不晚于查询时刻，生成后未超过 168 小时。累计值取最新一份。
- 成功率按受理时间归属；成功/两次模拟失败计入分母，PROCESSING 与 ABORTED 分别展示并排除。AC7 的审批撤销终止属于 ABORTED。
- 日期汇总分别提供作品指标和受理任务统计；保留北京时间零值日期，包括滚动窗口涉及的边缘日期。

## 验证

标准库 unittest + FastAPI TestClient + jsonschema；可控时钟、临时文件数据库和真实本地 HTTP 替身验证。固定 10,000 个键的第一尝试失败比例接受 8%–12%。故障恢复不实际等待时间；并发测试使用多个独立数据库连接。
