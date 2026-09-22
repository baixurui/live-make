# 共享契约

所有服务必须以本目录为跨模块接口的唯一来源。修改必须同步更新生产方、消费方和契约测试。

- `openapi.yaml`：控制台和内部服务的 HTTP API。
- `enums.json`：任务状态和风险枚举。
- `events/v1`：异步事件 JSON Schema。
- `publishing-insights.openapi.json`：发布/回执/事件确认及指标接口的完整定义，由模块导出，根 OpenAPI 引用。包含业务 API 已实现的可信发布上下文端点。

发布模块只发送最终成功或失败事件。`task.failed.v1` 的 `failed_step=publishing` 时 `retryable=false`、`attempt<=2`；工作流不得再次重试发布，负责转 `PAUSED` 后确认事件。发布请求的原有三个必需 payload 字段保留，新增可选账号/媒体版本声明必须与可信业务记录一致。

发布媒体版本统一为正整数。最终发布失败必须带 `receipt_id` 和 `request_key`（原发布请求幂等键）；`attempt` 为模块实际调用次数 0–2，不采用其他工作流节点的零基重试编号。上下文读取使用专用 Bearer 凭据，不能通过 X-Internal-Service 头获取。
