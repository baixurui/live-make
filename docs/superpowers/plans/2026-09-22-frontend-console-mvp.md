# 前端控制台 MVP 实现计划

> **面向 AI 代理的工作者：** 必须使用 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development` 逐任务实现本计划。

**目标：** 在 `apps/web` 交付可操作的六页控制台 MVP，并对 HIGH 双账号审批门禁提供自动化保护。

**架构：** 使用 React + TypeScript 单页应用。`src/domain` 保存任务、权限和审批纯规则，`src/data` 通过业务 API 适配器提供可替换的演示数据，`src/pages` 组合六个页面。浏览器只访问 `/api/v1` 业务 API；未配置后端时使用内存演示数据。

**技术栈：** Vite、React、TypeScript、Vitest、React Testing Library。

---

### 任务 1：初始化前端工程和测试运行器

**文件：** `apps/web/package.json`、`apps/web/vite.config.ts`、`apps/web/tsconfig.json`、`apps/web/src/main.tsx`、`apps/web/src/App.tsx`、`apps/web/src/styles.css`

- [ ] 编写一个渲染控制台标题的失败组件测试。
- [ ] 运行 `npm test -- --run`，确认因工程/组件缺失失败。
- [ ] 创建最小 Vite React 工程和测试配置，令测试通过。
- [ ] 运行 `npm test -- --run` 与 `npm run build`。

### 任务 2：实现任务审批领域规则

**文件：** `apps/web/src/domain/approval.ts`、`apps/web/src/domain/approval.test.ts`

- [ ] 编写 LOW 单人批准可排期、HIGH 必须两个不同账号批准、拒绝不可排期的失败测试。
- [ ] 运行 `npm test -- --run src/domain/approval.test.ts`，确认缺少模块失败。
- [ ] 实现最小审批规则与状态说明。
- [ ] 重新运行领域测试。

### 任务 3：实现业务 API 适配器和演示数据

**文件：** `apps/web/src/data/api.ts`、`apps/web/src/data/demo-data.ts`、`apps/web/src/data/api.test.ts`

- [ ] 编写 API 基地址与登录请求通过 `/api/v1` 的失败测试。
- [ ] 实现 API 客户端及后端不可用时的显式演示数据回退。
- [ ] 为主题、推荐、任务、审批和指标提供与版本化契约对应的读取/动作接口。
- [ ] 运行适配器测试。

### 任务 4：交付六页控制台及权限/状态体验

**文件：** `apps/web/src/pages/*.tsx`、`apps/web/src/components/*.tsx`、`apps/web/src/App.test.tsx`、`apps/web/src/App.tsx`、`apps/web/src/styles.css`

- [ ] 编写失败测试：可导航到六页；HIGH 任务在不足两名不同批准人时显示不可排期原因。
- [ ] 实现登录、主题管理、推荐池、任务详情与审批、形象/音色设置、七日指标看板。
- [ ] 在页面中显示加载、空数据、失败和无权限状态；管理员专有操作按角色门禁。
- [ ] 运行前端测试。

### 任务 5：验证可交付性

**文件：** `README.md`

- [ ] 记录前端启动、测试、构建命令与演示账户说明。
- [ ] 运行 `npm test -- --run`、`npm run build`、`python -m unittest tests/contracts/test_shared_contracts.py -v`。
- [ ] 检查 `git status --short` 和变更范围，确认未改动共享 API/枚举契约、未添加密钥。
