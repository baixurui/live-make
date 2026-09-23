# Live Make

公司内部视频生成与运营平台 MVP。

## 网页工作台

已实现中文登录、任务列表、上传/预览/下载、真实模板视频生成和带审核确认的抖音接入。

```powershell
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

打开 `http://127.0.0.1:8080`。首次启动会在终端显示随机管理员密码，或通过环境变量/`.env` 设置。已有账号不自动重置。数据库和素材保存在 `.runtime/`。

模板合成可本地使用；百炼已接入 `qwen3.6-plus → wanx-v1 → wan2.7-i2v`，在 `.env` 填写 `DASHSCOPE_API_KEY` 并重启即可进入三步生成，详见 [百炼配置](docs/bailian-setup.md)。抖音仍需自己的应用权限、HTTPS 回调和账号授权；未配置时发布按钮禁用，没有虚假成功。详细操作、安全边界及测试见 [工作台运行说明](docs/studio-operations.md)。

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
