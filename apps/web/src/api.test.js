import test from "node:test";
import assert from "node:assert/strict";
import { createApi } from "./api.js";

test("uses the versioned business API for login", async () => {
  const requests = [];
  const api = createApi(async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ access_token: "token" }) };
  });

  await api.login("admin@example.com", "demo");

  assert.equal(requests[0].url, "/api/v1/auth/login");
  assert.equal(requests[0].options.method, "POST");
});

test("returns demo data when the business API is unavailable", async () => {
  const api = createApi(async () => { throw new Error("offline"); });
  const result = await api.getTopics();
  assert.equal(result.source, "demo");
  assert.equal(result.data[0].name, "AI 生产力");
});
