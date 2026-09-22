# 模拟发布与指标服务

Issue #14。实现已排期任务的模拟发布、持久化幂等回执、一次自动重试、可靠最终事件、三个阶段指标快照与最近 7 天聚合。

## 本地验证

在仓库根目录执行（Python 3.11+）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\services\publishing-insights[test]"
.\.venv\Scripts\python.exe -I services/publishing-insights/tests/test_publishing.py
.\.venv\Scripts\python.exe -I tests/contracts/test_shared_contracts.py -v
```

测试使用临时文件数据库、受控时钟和业务状态替身，不需要真实平台、工作流或服务凭据，不会等待实际的 7 天。

## 启动

安装完成后设置环境变量：

```powershell
$env:WORKFLOW_PUBLISHING_TOKEN = '<至少16字符的工作流专用凭据>'
$env:METRICS_TOKEN_SCOPES = '{"<不同的至少16字符指标凭据>":["account-1"]}'
$env:BUSINESS_API_URL = 'http://localhost:8000'
$env:BUSINESS_API_TOKEN = '<业务API内部读取凭据>'
$env:PUBLISHING_DATABASE_PATH = 'services/publishing-insights/data/publishing.sqlite3'
python -m publishing
```

服务默认端口 8014；`/healthz` 验证数据库可用，`/docs` 提供接口说明。健康检查不代表业务 API 或工作流已接入。内置后台循环默认每 5 秒恢复处理中发布、生成到期快照并清理过期数据，可通过 `PUBLISHING_WORKER_INTERVAL` 配置。

也可复制模块 `.env.example` 为 `.env` 并填入私有值，在模块目录执行 `docker compose up --build -d`。Compose 提供独立持久卷；此模块使用 SQLite，不使用根 Compose 的 PostgreSQL。当前支持单机共享同一数据库文件的并发处理，不支持多主机部署。

## 与业务 API 对接

发布器始终从业务 API 获取可信状态，不能通过请求携带 `approved=true` 绕过审批。业务 API 需要实现：

`GET /api/v1/internal/tasks/{task_id}/publishing-context`

出站使用 `Authorization: Bearer BUSINESS_API_TOKEN`，5 秒超时、不跟随重定向。响应形状在 `contracts/publishing-insights.openapi.json` 的 `TaskContext` 定义，包含：

```json
{
  "task_id": "task-1",
  "account_id": "account-1",
  "status": "SCHEDULED",
  "scheduled_at": "2026-09-22T12:00:00+08:00",
  "media_version": "media-1",
  "approved_media_version": "media-1",
  "script_approved": true,
  "video_approved": true,
  "qc_passed": true,
  "risk_level": "LOW",
  "qc_score": 80,
  "review_score": 70
}
```

允许 `SCHEDULED`/`PUBLISHING`，两个审批和质检必须通过，审批媒体版本必须匹配；`BLOCKED` 禁止发布。每次实际尝试重新读取。字段缺失、类型错误或读取故障均不能放行。

## 与工作流对接

所有 `/api/v1/internal/publishing/*` 请求带工作流凭据。北京时间 12:00 发出以下事件到 `POST /api/v1/internal/publishing/requests`：

```json
{
  "event_id": "request-event-1",
  "event_type": "task.publish_requested.v1",
  "occurred_at": "2026-09-22T12:00:00+08:00",
  "correlation_id": "task-flow-1",
  "idempotency_key": "publish-task-1-media-1",
  "data": {
    "task_id": "task-1",
    "receipt_id": "receipt-1",
    "scheduled_at": "2026-09-22T12:00:00+08:00"
  }
}
```

以上时间仅为示例，实际请求必须使用任务当天排期。允许当日延迟到达，禁止提前或跨日首次执行。同账号同排期日、任务、回执、幂等键都有持久化唯一约束。相同业务请求重放可换 envelope 的事件 ID，原 correlation_id 用于结果追踪；关键业务内容变化返回 409。

响应是回执：`SUCCEEDED`、`FAILED`、`ABORTED` 返回 200；依赖暂不可用、已受理待恢复时 `PROCESSING` 返回 202。未受理的依赖故障为 503，工作流可重投同一请求，不可换键创建第二个发布。`GET /api/v1/internal/publishing/receipts/{receipt_id}` 可查询。

工作流需要轮询 `GET /api/v1/internal/publishing/events?limit=100`：

1. 按 `event_id` 去重，并在自己的事务中保存事件处理记录与任务状态。
2. `task.published.v1` → 更新 `PUBLISHED`。
3. `task.failed.v1` 且 `failed_step=publishing` → 更新 `PAUSED`（若任务已取消等终态，由工作流校验合法迁移）。`retryable=false`，不可再尝试发布。
4. 状态更新落盘后，调用 `POST /api/v1/internal/publishing/events/{event_id}/ack`，成功返回 204，可重复确认。

未确认事件会重复返回相同 event_id。模块不会直接调用全局状态迁移接口，也不会在首个模拟失败后发出会触发外部重试的事件。**一次重试只属于本模块，转 PAUSED 只属于工作流。**

## 指标与前端

业务网关使用绑定账号集合的指标凭据请求：

`GET /api/v1/metrics?account_id=account-1`

指标凭据不得下发浏览器。网关完成用户权限判断后选用对应账号范围的凭据；本服务拒绝范围外账号，工作流凭据也不能读指标。

响应包含 `window_start/window_end/as_of`、`metrics`（plays/likes/comments/favorites/shares/followers）、`published_count`、`snapshot_count`、`outcomes` 及 `daily_metrics/daily_outcomes`。OpenAPI 给出完整类型。

- 最近 168 小时采用 `[start, end)`；按北京时间日期分组，滚动窗口可能涉及 8 个自然日。
- 作品取最新一份累计快照；绝不把 1 小时、24 小时、7 天累计值相加。尚未生成快照的作品指标为零。
- 成功率为受理窗口内 `succeeded / (succeeded + failed)`，0–1 比例；分母为零为 null。处理中和资格撤销终止分别显示 processing/aborted，不进分母。
- 快照从实际生成起保留 168 小时；阶段完成标记、幂等回执和最终事件保留，防止清理后重新生成或发布。
- 模拟输入和种子随发布固定，结果可复现；每次模拟调用约 10% 失败。两个阶段的失败判定独立，固定输入仍确定。评分缺失使用 50 的中性基准，快照保存 degraded 标记。

## 契约与部署边界

`contracts/openapi.yaml` 引用 `contracts/publishing-insights.openapi.json`。修改接口后在模块目录运行 `python export_contracts.py` 并执行测试，保证静态契约与运行时一致。事件 schema 原必需字段保留，发布请求现在严格校验支持的字段；调用方需按契约提交。

SQLite 事务仅覆盖无外部副作用的模拟器。事务回滚后可能重新计算相同模拟结果，但不会新增逻辑作品、消耗额外已提交尝试或重复回执。替换真实平台适配器时必须增加平台幂等/结果查询对账，不能宣称数据库事务能够回滚外部发布。

真实业务上下文提供方、工作流状态更新和前端尚未在本分支实现。测试证明本模块行为与受控对接，真实联调完成后才可将完整跨模块验收标记通过。
