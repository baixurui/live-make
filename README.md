# Live Make

公司内部视频生成与运营平台 MVP。

## MVP 约束

- 每日北京时间 09:00 搜索、11:30 截止审核、12:00 模拟发布。
- 单账号每天 1 条，竖屏 720×1280、7–15 秒。
- 风险等级仅为 `LOW`、`HIGH`、`BLOCKED`。
- 所有服务遵循 `contracts/` 中的 API、事件和状态枚举。

## 目录

- `apps/web`：前端控制台。
- `services`：业务 API、工作流、内容智能、媒体生产、发布与指标服务。
- `contracts`：跨模块 API、事件和枚举的唯一来源。
- `tests/contracts`：共享契约校验。

## 验证

```powershell
python -m unittest tests/contracts/test_shared_contracts.py -v
```

## Frontend console

The MVP console is in `apps/web`. It uses the `/api/v1` business API boundary only; when the API is not deployed, it presents local demonstration data and does not connect to search, LLM, media, or publishing providers.

```powershell
cd apps/web
npm test
npm run build
npm run dev
```

Open `http://localhost:5173`. The task detail page demonstrates that a HIGH-risk task remains unschedulable until two different accounts approve it.
