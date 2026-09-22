# 验收清单

每项由自动化行为测试或明确的联调结果证明，不以文件存在代替验收。

- [x] AC1：可信工作流、审批、风险、排期、媒体版本与错误请求资格验证。
- [x] AC2–AC4：相同请求并发/重启重放唯一、同账号每日限制、换键/同键冲突。
- [x] AC5：稳定复现三种模拟结果；固定大样本失败率 8%–12%。
- [x] AC6–AC7（模块侧）：仅一次重试，恢复不重置次数，审批撤销终止；最终失败发不可重试事件。全局 PAUSED 状态见真实联调项。
- [x] AC8（模块侧）：最终结果和 outbox 同时落盘，重复拉取稳定，确认幂等；工作流应用事件的事务去重需联调。
- [x] AC9：替换受控发布器后仍返回相同回执结构。
- [x] AC10–AC11：三个到期阶段、延迟补齐、累计递增、评分规律与降级记录。
- [x] AC12–AC14：最新快照聚合、正确成功率、滚动时间边界、北京时间分组和账号隔离。
- [x] AC15：快照保留期、清理后不重建、旧幂等回执仍可重放。
- [x] AC16（本地）：HTTP 请求到回执、事件、快照、看板完整闭环。
- [x] 共享契约及模块全部自动化测试通过。
- [ ] 真实联调：业务 API 提供可信上下文，工作流消费事件并实际转 PAUSED，前端使用真实指标响应；其他模块负责人接入后执行。

## 验证记录

2026-09-22，本机 Python 3.11：

- `python -I services/publishing-insights/tests/test_publishing.py`：31/31 通过；包括真实本地 HTTP 上下文服务器、FastAPI HTTP 闭环、后台生命周期、并发及重启恢复、静态契约与运行时一致性。
- `python -I tests/contracts/test_shared_contracts.py -v`：3/3 通过。
- `python -m ruff check services/publishing-insights`：通过。
- `python -m compileall -q services/publishing-insights/publishing`：通过。
- `docker compose -f services/publishing-insights/compose.yaml config --no-interpolate --quiet`：通过；Docker 引擎可用。本次未构建镜像或启动容器。

边界：SQLite 事务保证模拟逻辑结果唯一，崩溃回滚可重新计算无副作用的模拟结果。真实发布平台需要额外幂等与对账设计。跨模块的真实生产方、消费方尚未实现，不能据此宣称 Issue 的全部联调验收已完成。
