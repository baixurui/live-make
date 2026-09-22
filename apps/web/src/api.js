const demo = {
  topics: [{ id: "topic-ai", name: "AI 生产力", enabled: true }, { id: "topic-trends", name: "行业热点", enabled: true }],
  recommendations: [{ id: "rec-low", title: "AI 助手在客服中的新趋势", source: "行业报告 / 官方公告", risk: "LOW" }, { id: "rec-high", title: "敏感行业政策解读", source: "公开政策原文", risk: "HIGH" }],
  metrics: { published: 18, passRate: "82%", duration: "26 分钟", failed: 2 },
  task: { id: "LM-20260922-01", status: "SCRIPT_PENDING_APPROVAL", risk: "HIGH", scripts: ["效率提升案例", "趋势观察", "风险提示"], evidence: "行业报告", media: "等待生成", quality: "待执行", receipt: "未发布" }
};

export function createApi(fetcher = fetch) {
  let token = "";
  const request = async (path, options = {}) => {
    const response = await fetcher(`/api/v1${path}`, { ...options, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...options.headers } });
    if (!response.ok) throw new Error(`API 请求失败 (${response.status})`);
    return response.json();
  };
  const withFallback = async (path, fallback) => { try { return { source: "api", data: await request(path) }; } catch { return { source: "demo", data: fallback }; } };
  return {
    async login(username, password) { try { const data = await request("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }); token = data.access_token; return { source: "api", data }; } catch { return { source: "demo", data: { access_token: "demo-token", account: { username, is_admin: true } } }; } },
    getTopics: () => withFallback("/topics", demo.topics),
    createTopic: (name, keywords = []) => request("/topics", { method: "POST", body: JSON.stringify({ name, keywords, enabled: true }) }),
    updateTopic: (topicId, changes) => request(`/topics/${topicId}`, { method: "PATCH", body: JSON.stringify(changes) }),
    getRecommendations: () => withFallback("/recommendations", demo.recommendations),
    createTask: (title, topicId, riskLevel = "LOW") => request("/tasks", { method: "POST", body: JSON.stringify({ title, topic_id: topicId, risk_level: riskLevel }) }),
    getMetrics: () => withFallback("/metrics", demo.metrics),
    getTask: (taskId) => withFallback(`/tasks/${taskId}`, { ...demo.task, id: taskId }),
    async approve(taskId, kind, decision, comment = "") {
      try { return { source: "api", data: await request(`/tasks/${taskId}/approvals`, { method: "POST", body: JSON.stringify({ kind, decision, comment }) }) }; }
      catch { return { source: "demo", data: { id: `demo-${Date.now()}` } }; }
    }
  };
}
