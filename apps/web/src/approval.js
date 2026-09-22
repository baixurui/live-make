export function schedulingState(riskLevel, approvals) {
  if (approvals.some((approval) => approval.decision === "REJECT")) return { allowed: false, reason: "存在拒绝意见，不能排期" };
  const approvers = new Set(approvals.filter((approval) => approval.decision === "APPROVE").map((approval) => approval.userId));
  if (riskLevel === "HIGH" && approvers.size < 2) return { allowed: false, reason: "HIGH 风险任务需两个不同账号一致批准后才能排期" };
  return { allowed: approvers.size > 0, reason: approvers.size ? "审批条件已满足" : "请先完成审批" };
}
