# 实施任务

按用户授权顺序实现；测试使用临时数据库，生产不自动填充审批替身。

| 顺序 | 文件 | 工作与验证 |
| --- | --- | --- |
| T1 | publishing/models.py、storage.py、pyproject.toml | 定义严格请求/上下文、回执和持久化约束；验证非法字段与重启读取。 |
| T2 | publishing/adapters.py | 稳定模拟发布、累计指标、HTTP 可信上下文读取；验证三个结果分支及失败比例。 |
| T3 | publishing/service.py | 资格校验、受理与幂等、一次重试；验证越权、并发、业务冲突、依赖故障和恢复。依赖 T1–T2。 |
| T4 | publishing/service.py | 最终事件可靠存储与幂等确认；验证结果和事件一致、重复拉取 event_id 不变。依赖 T3。 |
| T5 | publishing/insights.py | 快照、阶段去重、过期清理和指标聚合；验证时间边界、累计去重和成功率。依赖 T3。 |
| T6 | publishing/api.py、__main__.py | 服务认证、范围控制、后台恢复和启动；验证 HTTP 授权、错误映射及生命周期。依赖 T4–T5。 |
| T7 | contracts/、tests/ | 补全共享 payload 类型和接口响应、契约回归测试；校验运行时 OpenAPI 与共享导出一致。依赖 T6。 |
| T8 | README.md、Dockerfile、compose.yaml、.env.example | 提供独立运行、持久化部署、工作流对接和指标调用方法；运行全部行为与共享契约测试。 |

真实工作流的 PAUSED 更新、业务 API 上下文读取端点以及前端真实展示由对应模块接入，交付时记录联调边界。

## 执行结果

T1–T8 已完成本模块实现与自动化验证，31 项模块测试及 3 项共享测试通过。运行命令、部署配置、契约和工作流对接说明已交付；Docker 镜像构建和真实跨模块联调尚未执行，详见 checklist.md。
