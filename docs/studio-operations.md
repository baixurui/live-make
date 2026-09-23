# 网页工作台运行与接入

## 本地启动

要求 Python 3.11+。Windows 默认使用微软雅黑；其他系统需要安装中文字体并设置 `STUDIO_FONT`。

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，设置 BUSINESS_API_ADMIN_PASSWORD；不要覆盖已有 .env。
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

打开 `http://127.0.0.1:8080`。`start-local.ps1` 读取 `.env`（已有进程环境变量优先），首次没有设置密码时生成密码并在终端显示；请及时保存。管理员仅在数据库无账号时创建，修改环境变量不会重置已有账号密码。直接 `python -m services.business_api.server` 也会读取项目根目录 `.env`。

数据默认保存在 `.runtime/business-api.sqlite3` 和 `.runtime/media/`。备份时同时备份数据库和文件；不要只复制 SQLite 文件而漏掉媒体。管理员可用既有 `/api/v1/admin/accounts` 接口创建成员账号，目前没有账号管理网页。所有已登录成员共享内部任务和媒体库。

## 网页功能

百炼文本→图片→视频功能现已接入，配置和逐步确认操作见 [百炼配置](bailian-setup.md)。下列模板制作仍可不使用 API Key 独立运行。

1. 登录 → 新建任务，填写名称和风险等级。
2. 上传 MP4/MOV/WebM（100 MiB、5 分钟、4K 以内），服务器验证并转码为 720×1280 MP4；网页显示处理状态和错误。
3. 预览、切换历史版本、拖动进度条或下载视频。文件不是仅存于浏览器的临时预览。
4. 使用 1–100 字文案生成 7–15 秒模板视频；可选已有素材作为背景。无背景是文字卡片，有背景则循环/裁剪并叠加字幕，保留背景音轨（如果有）。不包含 AI 文生视频、自动配音、素材搜索或自动音乐。
5. 看过最新成片后填写发布文案并审核。低风险一人，高风险两个不同且启用的账号，已阻止任务不允许制作/发布。修改文案或视频版本后旧审核失效。
6. 配置并授权抖音后，管理员选择可见性并明确确认才发起真实提交。默认仅自己可见，公开需要主动选择。

制作队列最多 4 个任务，单线程执行，子进程最长 180 秒。服务重启会将未完成任务标为失败；保留原素材版本。手动工作台不改变原有自动工作流状态，因此任务可显示“待制作”而已有视频版本。已注册到自动工作流的任务不允许从本工作台修改或发布，以免绕过自动审核和排期。手动提交不使用自动工作流的 12:00 排期，但保留每账号北京时间每日最多一次提交的限制。

## 抖音真实接入

需要自己的开放平台应用、`video.create.bind` 权限和账号授权。本次代码对接官方接口，但没有使用真实应用凭据进行在线发布验收，也没有自动发布任何作品。

服务端配置：

```dotenv
DOUYIN_CLIENT_KEY=你的应用Key
DOUYIN_CLIENT_SECRET=你的应用Secret
DOUYIN_REDIRECT_URI=https://你的域名/api/v1/studio/douyin/callback
STUDIO_COOKIE_SECURE=1
```

抖音官方授权文档要求 HTTPS 回调，且必须与应用登记一致、不携带自定义查询参数。需要让 HTTPS 域名反向代理到此服务；从这个同一 HTTPS 域名登录工作台，再点击平台接入中的“连接抖音账号”，否则 localhost 会话 Cookie 不会出现在另一个域名的回调中。反向代理须保留 Host。不要直接暴露此开发用 HTTP 服务。

已对照官方文档（2026-09-23）：

- 授权：`https://open.douyin.com/platform/oauth/connect/`
- 换取令牌：`https://open.douyin.com/oauth/access_token/`，form 编码。
- 视频上传：`https://open.douyin.com/api/douyin/v1/video/upload_video/`，multipart `video`。
- 创建视频：`https://open.douyin.com/api/douyin/v1/video/create_video/`，`private_status=1` 自见、`0` 公开。
- 文档：`https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/video-create`
- 上传文档：`https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/upload-video`
- 授权文档：`https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/account-permission/douyin-get-permission-code`

令牌保存在本地 SQLite，不下发到浏览器；本地版本没有磁盘加密，必须保护运行目录和备份，公网部署应改为加密凭据存储。授权到期重新连接，不自动刷新。

`SUBMITTING` 表示处理中；`SUBMITTED` 仅表示拿到平台 item_id、仍待平台审核；`UNKNOWN` 表示网络/权限/平台结果未确认，需人工查看抖音作品列表。任何已提交或不确定记录均锁定当天名额，不自动重试或自动解锁。页面不伪造播放量、发布成功或公开状态。未提供发布后审核结果轮询、删除作品或每日自动发布。

## 测试

```powershell
python -m unittest discover -s tests/contracts -v
python -m unittest discover -s tests/business_api -v
python -m unittest discover -s tests/workflow -v
python -m unittest discover -s tests/studio -v
node --check apps/web/app.js
```

浏览器检查需要本机 Chrome，`python -m pip install playwright`，以及已启动服务：

```powershell
$env:STUDIO_TEST_PASSWORD='本地管理员密码'
python -m tests.studio.browser_smoke
```

浏览器检查会创建真实测试任务和视频，输出截图/下载到 `.runtime/browser-check/`；不进行真实抖音发布。测试不覆盖真实供应商账号权限、平台审核或 HTTPS 部署。

## 部署边界

这是内部本地 MVP。上线前需完善生产 HTTP 服务、限流/登录防爆破、配额与媒体清理、加密密钥管理、HTTPS、备份及权限隔离。原有 internal API 的服务身份仍是受信请求头，必须置于受保护网络，禁止通过公网代理暴露 `/api/v1/internal/`。不要将 `.env`、SQLite、运行目录或完整仓库上传到静态网站托管服务。
