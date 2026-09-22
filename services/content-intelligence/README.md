# Content Intelligence — 离线推荐模块

Issue #12 的第一批实现：不可变主题、来源及调用审计模型，搜索 Provider 端口，固定结果测试适配器，以及推荐去重、相关性排序和最多 10 条的规则。

## 环境

- Python 3.11 或更高版本；仅使用标准库。
- 无需第三方依赖、网络、数据库、Docker 或 API Key。
- 在仓库根目录执行下方命令。当前模块通过 `PYTHONPATH` 导入，不需要安装。

## 接口与行为

- `SearchProvider.search(query: str, limit: int) -> SearchBatch`：返回来源元组和调用审计，空结果也保留审计。
- `RecommendationService.recommend(topic: Topic, scan_date: date) -> RecommendationBatch`：从主题关键词构建查询并生成推荐批次。
- 禁用主题不调用供应商，返回空推荐且 `call=None`。
- 启用主题每次调用只执行一次搜索，请求 `limit=10`；即使供应商超量返回，本模块也最多保留 10 条。
- 相关性分数越高越靠前；按原始 URL 精确去重，保留分数最高的结果。相同分数按 URL、标题、摘要和来源时间字符串确定稳定顺序。
- 不会合并不同查询参数的 URL，也不在此阶段抓取网页或校验事实。
- 推荐 ID 由主题 ID、扫描日期和 URL 确定性生成。推荐 ID 仅用于后续结果提交，不代表记录已落库。
- `scan_date` 由调用方按业务时区传入；服务不读取当前日期、不调度、不做每天执行次数控制。
- `CallRecord` 保留调用 ID、供应商、起止时间、可选模型/提示词版本/token 数/成本和币种，耗时通过 `latency_ms` 获取。
- 起止时间按 UTC 对应的实际时刻校验，耗时使用整数毫秒，避免夏令时切换和浮点截断造成误差；原始时区信息保持不变。
- 未知来源时间、模型、token 数和成本使用 `None`；已知成本使用 `Decimal` 并提供三字母币种，不伪造费用或发布时间。
- 来源 URL 必须有 HTTP(S) 协议和主机，禁止携带用户名/密码。所有非空时间带时区，非有限相关性分数被拒绝。
- 畸形 URL 解析失败时只抛出固定错误信息，并抑制原始解析异常链，避免在普通异常日志中暴露 URL 凭据。
- 搜索失败抛出 `ProviderError`，仅携带固定错误码 `SEARCH_UNAVAILABLE`、`retryable` 和调用审计，不自动重试、不伪装成成功空结果。
- `FakeSearchProvider` 返回注入的固定批次或固定失败，并在 `calls` 记录调用。它故意不截断 fixture，以测试领域服务处理供应商超量返回的行为。

## 使用示例

在测试命令相同的 `PYTHONPATH` 环境下使用：

```python
from datetime import date, datetime, timezone

from content_intelligence.adapters.testing import FakeSearchProvider
from content_intelligence.models import CallRecord, SearchBatch, SearchResult, Topic
from content_intelligence.recommendation_service import RecommendationService

moment = datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)
provider = FakeSearchProvider(
    SearchBatch(
        results=(
            SearchResult(
                url="https://example.test/article/1",
                title="人工智能课程",
                summary="用于离线测试的固定摘要，不是实际搜索结果。",
                relevance=0.9,
                source_time=moment,
            ),
        ),
        call=CallRecord("example-call", "fake-search", moment, moment),
    )
)
topic = Topic("topic-ai", "人工智能", ("人工智能", "课程"))
result = RecommendationService(provider).recommend(topic, date(2026, 9, 22))
print(result.recommendations[0].recommendation_id)
```

## 测试

在仓库根目录运行，命令结束后会恢复原有 `PYTHONPATH`：

```powershell
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $PWD 'services/content-intelligence/src'
    python -m unittest discover -s tests/content_intelligence -v
    if ($LASTEXITCODE -ne 0) { throw 'Content intelligence tests failed' }
    python -m unittest discover -s tests/contracts -v
    if ($LASTEXITCODE -ne 0) { throw 'Shared contract tests failed' }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
```

分别发现两个测试目录，避免根 `tests` 缺少包入口时出现零测试误通过。

## 模块边界

本模块返回 `RecommendationBatch`，不写数据库、不发送事件、不修改任务状态。
推荐每日替换、已选推荐保留、结果持久化及幂等提交由业务 API 与工作流对接时确认；执行时间和重试由工作流负责。

本批不包含真实博查/LLM、三份脚本候选、事实核验或风险审核、审批、媒体与发布，不能据此关闭整个 Issue #12。
测试适配器不是博查客户端；真实适配器接入时需单独验证供应商响应解析、超时、凭据注入和异常清洗，不应把原始供应商异常或认证信息放进领域输出。
共享契约没有修改；接入落库接口后才能发布引用已持久化推荐 ID 的 `topic.discovered.v1`。
