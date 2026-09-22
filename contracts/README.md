# 共享契约

所有服务必须以本目录为跨模块接口的唯一来源。修改必须同步更新生产方、消费方和契约测试。

- `openapi.yaml`：控制台和内部服务的 HTTP API。
- `enums.json`：任务状态和风险枚举。
- `events/v1`：异步事件 JSON Schema。
