import test from "node:test";
import assert from "node:assert/strict";
import { schedulingState } from "./approval.js";

test("LOW task can schedule after one approval", () => {
  assert.equal(schedulingState("LOW", [{ userId: "alice", decision: "APPROVE" }]).allowed, true);
});

test("HIGH task needs two distinct approvals", () => {
  const state = schedulingState("HIGH", [{ userId: "alice", decision: "APPROVE" }]);
  assert.equal(state.allowed, false);
  assert.match(state.reason, /两个不同账号/);
});

test("rejection always prevents scheduling", () => {
  assert.equal(schedulingState("LOW", [{ userId: "alice", decision: "REJECT" }]).allowed, false);
});
