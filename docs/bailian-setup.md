# 百炼三步生成：填 Key 后使用

## 只需填写一项

项目根目录已创建 git 忽略的 `.env`，填写：

```dotenv
DASHSCOPE_API_KEY=你的北京地域百炼APIKey
```

已预设以下模型，不会自动换成其他模型或切换地域：

```dotenv
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com
BAILIAN_TEXT_MODEL=qwen3.6-plus
BAILIAN_IMAGE_MODEL=wanx-v1
BAILIAN_VIDEO_MODEL=wan2.7-i2v
```

保存后停止旧服务，再从项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

也可运行 `python -m services.business_api.server`，现在两种方式均读取项目根目录 `.env`。已有进程环境变量优先于文件；若修改文件后不生效，请检查终端是否设置了同名变量。不需要安装 OpenAI/DashScope SDK，不需要 OSS 或公网暴露本地图片。

打开 `http://127.0.0.1:8080`，现有账号和数据不变。「平台接入」会显示 Key 是否配置，但不会显示 Key，也不会未经确认调用模型来验证权限。配置成功不等于账号已开通模型、余额充足或配额可用。

## 网页操作

1. 新建/进入任务 → 「百炼创作流水线」。输入主题，点击生成文案，确认可能产生的费用。
2. `qwen3.6-plus` 返回文案、图片提示词、视频提示词、发布标题。可编辑画面描述，再确认生成一张图片。
3. `wanx-v1` 生成 `720*1280` 竖屏首帧。网页预览真实图片并保存本地，不满意可修改描述重新生成（新的调用可能计费）。
4. 看过并确认最新首帧后，编辑动作/运镜，点击生成视频。`wan2.7-i2v` 使用新版 `input.media=[{type:first_frame,url:...}]` 协议，通过 Base64 传入本地图片。
5. 默认 8 秒、720P；可选 10 或 15 秒，保留模型 AI 水印。视频下载后转为兼容浏览器的 720×1280 MP4，加入现有成片列表，可预览、下载、审核。
6. 仍需单独的抖音配置和人工发布确认，不会生成后自动发布。

当前实现是单镜头首帧生成，不含多分镜拼接、锁定口播配音、声音克隆。文本中的口播文案仅为策划输出，不保证视频音轨朗读该文案；图片与视频模型输出还需人工检查。

## 任务恢复与费用控制

- 每次阶段调用都有显式确认；没有自动连续生成，不自动重试付费 POST。
- 图片和视频返回远端 ID 后立即持久化，通过 GET 查询状态。应用内显示远端任务 ID，可去百炼控制台排查。
- 查询最长 15 分钟，网络中断/下载失败/服务重启后可点击「恢复查询原任务」，不重新生成。
- 未返回 task_id 的超时或服务端异常记为 UNKNOWN，锁住新生成。管理员必须先核对百炼控制台和费用，再点击解锁。解锁不取消远端任务、不退款。
- 百炼远端任务及临时结果有有效期；过期后不能保证恢复。成功结果会下载保存在 `.runtime/media`，不要依赖云端临时地址长期预览。
- 队列与原模板制作共用单工作线程、最多四项。BLOCKED/已发布/取消任务和自动工作流托管任务不能绕过既有约束调用生成。
- 文案与提示词会发送到阿里云，确认后再提交敏感业务内容。

## 模型名称与官方接口

本次保留用户要求的 `wan2.7-i2v` 名称。官方当前新版协议示例使用快照名 `wan2.7-i2v-2026-04-25`；若你的账号返回 ModelNotFound/InvalidModel，请在百炼控制台确认可用名称，按需把 `BAILIAN_VIDEO_MODEL` 改为该快照并重启。代码不会自动切换模型或再次发起计费调用。

默认旧北京域名目前仍受官方支持。如需业务空间专属域名，仅改为 `https://你的WorkspaceId.cn-beijing.maas.aliyuncs.com`，不附加 `/api/v1` 或 `/compatible-mode/v1`。不支持在此配置下跨地域使用 wanx-v1。

对照官方文档（2026-09-23）：

- Qwen：`https://help.aliyun.com/zh/model-studio/qwen3-6-plus`
- 思考模式与非思考 JSON 输出：`https://help.aliyun.com/zh/model-studio/deep-thinking`
- wanx-v1：`https://help.aliyun.com/zh/model-studio/text-to-image-api-reference`
- Wan 2.7：`https://help.aliyun.com/zh/model-studio/image-to-video-general-api-reference`

## 验证范围

已验证请求结构、未配置保护、确认门槛、异步状态、恢复查询不重复提交、图片本地保存与认证、视频渲染保存和浏览器三步交互。运行：

```powershell
python -m unittest discover -s tests/studio -v
python -m tests.studio.browser_ai_smoke
```

浏览器测试使用本地模拟的供应商，不调用真实百炼、不产生费用。真实账号权限、模型别名可用性、余额、生成效果和供应商响应必须在填写 Key 后进行一次人工确认的真实验收。密钥只留在服务器 `.env`/进程环境，不写入任务数据，不下发到网页。
