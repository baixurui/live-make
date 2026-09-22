import test from "node:test";
import assert from "node:assert/strict";
import { createApi } from "./api.js";

test("uses the versioned business API for login", async () => {
  const requests = [];
  const api = createApi(async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ access_token: "token" }) };
  });

  const result = await api.login("admin@example.com", "demo");

  assert.equal(requests[0].url, "/api/v1/auth/login");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(result.source, "api");
});

test("returns a marked demo session when login API is unavailable", async () => {
  const api = createApi(async () => { throw new Error("offline"); });
  const result = await api.login("admin@example.com", "demo");
  assert.equal(result.source, "demo");
  assert.equal(result.data.role, "ADMIN");
});

test("sends the saved login token with later business API requests", async () => {
  const requests = [];
  const api = createApi(async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => url.endsWith("/login") ? ({ access_token: "saved-token" }) : [] };
  });

  await api.login("admin@example.com", "demo");
  await api.getTopics();

  assert.equal(requests[1].options.headers.authorization, "Bearer saved-token");
});

test("returns demo data when the business API is unavailable", async () => {
  const api = createApi(async () => { throw new Error("offline"); });
  const result = await api.getTopics();
  assert.equal(result.source, "demo");
  assert.equal(result.data[0].name, "AI 生产力");
});

test("reads recommendations and metrics from versioned API endpoints", async () => {
  const urls = [];
  const api = createApi(async (url) => {
    urls.push(url);
    return { ok: true, json: async () => [] };
  });

  await api.getRecommendations();
  await api.getMetrics();

  assert.deepEqual(urls, ["/api/v1/recommendations", "/api/v1/metrics"]);
});

test("falls back to the demo task when task detail API is unavailable", async () => {
  const api = createApi(async () => { throw new Error("offline"); });
  const result = await api.getTask("LM-20260922-01");
  assert.equal(result.source, "demo");
  assert.equal(result.data.id, "LM-20260922-01");
});

test("submits an approval decision to the task approval endpoint", async () => {
  const requests = [];
  const api = createApi(async (url, options) => {
    requests.push({ url, options });
    return { ok: true, status: 201, json: async () => ({ id: "approval-1" }) };
  });

  await api.approve("task-1", "APPROVE");

  assert.equal(requests[0].url, "/api/v1/tasks/task-1/approvals");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.body, JSON.stringify({ decision: "APPROVE" }));
});

test("returns a demo approval when the approval API is unavailable", async () => {
  const api = createApi(async () => { throw new Error("offline"); });
  const result = await api.approve("task-1", "APPROVE");
  assert.equal(result.source, "demo");
});
