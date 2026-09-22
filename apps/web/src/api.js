const demo = {
  topics: [{ id: "topic-ai", name: "AI 生产力", enabled: true }, { id: "topic-trends", name: "行业热点", enabled: true }],
  recommendations: [{ id: "rec-low", title: "AI 助手在客服中的新趋势", source: "行业报告 / 官方公告", risk: "LOW" }, { id: "rec-high", title: "敏感行业政策解读", source: "公开政策原文", risk: "HIGH" }],
  metrics: { published: 18, passRate: "82%", duration: "26 分钟", failed: 2 }
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
    async login(username, password) { const data = await request("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }); token = data.access_token; return data; },
    getTopics: () => withFallback("/topics", demo.topics),
    getRecommendations: () => withFallback("/recommendations", demo.recommendations),
    getMetrics: () => withFallback("/metrics", demo.metrics),
    getTask: (taskId) => request(`/tasks/${taskId}`),
    approve: (taskId, decision) => request(`/tasks/${taskId}/approvals`, { method: "POST", body: JSON.stringify({ decision }) })
  };
}
