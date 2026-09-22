# 内容智能第一批实施计划：离线可验证的推荐模块

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 Provider 端口、确定性搜索测试适配器、推荐数据模型和带审计关联的推荐服务；先完成无需真实密钥、无需其他模块上线的可测试切片。

**Architecture:** 领域服务只依赖搜索端口；测试适配器注入固定搜索结果和调用审计。服务返回推荐批次，不执行定时任务、不写业务数据库、不修改任务状态、不发出引用尚未持久化记录的事件。

**Tech Stack:** Python 3.11 标准库、dataclasses、typing.Protocol、decimal.Decimal、unittest；本批不安装第三方依赖。

**Spec:** `docs/superpowers/specs/2026-09-22-content-intelligence.md`

## 执行记录（2026-09-22）

- 已同步远程 `main`，从 `cecb5f8` 创建 `feat/content-intelligence`，在当前目录完成 Task 1–3；未提交、未推送。
- TDD 记录：模型导入失败后 9 项通过；端口导入失败后 13 项通过；服务导入失败后 21 项通过。
- 独立审查发现 URL 解析错误可能暴露凭据，以及跨时区偏移变更的耗时计算问题；已分别添加失败回归并修复。
- URL 解析错误改为固定安全消息并抑制原异常链；审计时刻按 UTC 比较并使用整数毫秒，保留原时区。
- 补充 1001 毫秒精度回归，避免浮点运算把结果截断为 1000 毫秒。下方实现示例同步修正。
- 修复后内容模块 26 项测试、共享契约 3 项测试通过；README 示例执行成功，未修改任何已有共享契约或其他服务。
- 独立审查者复核了全部 5 项针对性回归，确认原有两个重要问题均已解决；未报告待处理的次要问题。
- 沿用当前工作区和 PowerShell 测试命令，不增加 worktree、依赖或自动提交。实际搜索、脚本、审核、结果落库和事件投递仍未实现。

## 范围和执行约束

- 本计划是 Issue #12 的第一批交付，不等于完成整个 Issue。
- 最多 10 条、相关性排序、来源与调用审计、确定性测试适配器是本批实现范围。
- 真实博查、真实 LLM、脚本候选、自动审核、结果落库和事件发布不属于本批。
- 这不是放弃完整规格；先交付可独立测试的推荐算法，再接跨模块接口和供应商。
- 不修改 `contracts/`、其他服务目录、根 Compose 或团队其他分支。
- 不自动提交、推送或创建 PR；分支创建须沿用用户确认的 `feat/content-intelligence`。
- 实现前读取适用的 AGENTS.md 和执行技能。若工作区已有用户修改，保留，不 reset 或覆盖。
- 推荐服务不保证调度次数：每天何时执行、搜索失败重试、人工重试配额都由工作流负责。
- 未选推荐每日替换、已选推荐保留及结果持久化由业务 API 负责，不在本模块暗建存储。
- 本批不输出 `LOW`、`HIGH` 或 `BLOCKED`，避免把搜索完整性误当成内容安全审核。

## 待本次计划确认的接口细化

规格里的 `SearchProvider.search(query, limit) -> list[SearchResult]` 是草案。建议改为返回 `SearchBatch`，包含 `results` 和 `call`：这样搜索为空时仍有调用审计，而不是把审计挂在某条推荐上导致丢失。

- `SearchProvider.search(query: str, limit: int) -> SearchBatch`
- `RecommendationService.recommend(topic: Topic, scan_date: date) -> RecommendationBatch`
- 未知来源时间、模型、token 数和成本使用 `None`；不能把未知费用伪装成免费，也不能编造发布时间。
- 成本使用 `Decimal` 和币种；供应商不提供时两者均为空。
- 排序和去重基于输入结果；不声称本批实现了真实博查相关性评分。
- URL 去重按原 URL 精确相等处理，不合并不同查询参数。只校验 HTTP(S) 地址和主机，不请求网页。
- 推荐 ID 从主题、扫描日期和 URL 确定性生成；这只是拟提交结果的 ID，不证明记录已经落库。

## 文件范围

| 文件 | 职责 |
| --- | --- |
| `services/content-intelligence/src/content_intelligence/__init__.py` | 包入口，保持为空 |
| `services/content-intelligence/src/content_intelligence/models.py` | 不可变主题、来源、调用、批次和推荐模型 |
| `services/content-intelligence/src/content_intelligence/ports.py` | 搜索端口和安全的供应商错误类型 |
| `services/content-intelligence/src/content_intelligence/adapters/__init__.py` | 适配器包入口，保持为空 |
| `services/content-intelligence/src/content_intelligence/adapters/testing.py` | 固定结果、固定失败的测试适配器 |
| `services/content-intelligence/src/content_intelligence/recommendation_service.py` | 排序、去重、截断和审计关联 |
| `tests/content_intelligence/test_recommendations.py` | 模型、适配器、服务的离线测试 |
| `services/content-intelligence/README.md` | 本批能力、边界和明确的运行命令 |

## Review Focus

1. 禁用主题不能调用供应商；空结果必须保留调用审计。Task 3 测试。
2. 超过 10 条、重复 URL、相同分数必须产生稳定的去重排序。Task 3 测试。
3. NaN、无穷分数、非 HTTP(S) URL、无时区时间必须拒绝。Task 1 测试。
4. 供应商失败不能被吞成成功空列表；异常和输出不得泄漏秘密。Task 2、3 测试。
5. 固定输入、日期和审计不应生成随机 ID、使用当前时间或修改适配器输入。Task 2、3 测试。

## 测试运行方式

根 `tests/` 目前不是递归可发现的完整 Python 包，不能只跑 `discover -s tests` 后看到 0 tests 就认为成功。本批分别运行两个测试目录，并检查实际用例数量。

在每次 PowerShell 测试命令中临时设置导入路径，完成后恢复：

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

---

## Task 1：数据模型和审计校验

**Files:** 创建两个包入口、`models.py`、`tests/content_intelligence/test_recommendations.py`。

**Interfaces:** 不消费其他任务产物；产出下列 dataclass，供 Task 2、3 使用。

- [x] **Step 1：写入以下测试，先确认导入失败。**

```python
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
from decimal import Decimal

from content_intelligence.models import CallRecord, SearchResult, Topic


MOMENT = datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)


def call_record() -> CallRecord:
    return CallRecord("call-1", "fake-search", MOMENT, MOMENT)


def source(index: int, score: float = 1.0) -> SearchResult:
    return SearchResult(
        f"https://example.test/articles/{index}",
        f"标题{index}",
        f"摘要{index}",
        score,
        MOMENT,
    )


class ModelTests(unittest.TestCase):
    def test_models_are_immutable_and_unknown_cost_is_not_zero(self):
        topic = Topic("topic-1", "人工智能", ("人工智能",), True)
        with self.assertRaises(FrozenInstanceError):
            topic.enabled = False
        self.assertIsNone(call_record().cost)
        self.assertIsNone(call_record().model)

    def test_rejects_invalid_sources(self):
        for score in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(score=score), self.assertRaises(ValueError):
                source(1, score)
        for url in ("file:///tmp/source", "https:///missing-host", "not-a-url"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                replace(source(1), url=url)
        with self.assertRaises(ValueError):
            replace(source(1), source_time=MOMENT.replace(tzinfo=None))

    def test_rejects_invalid_audit(self):
        for change in (
            {"cost": Decimal("-1"), "currency": "CNY"},
            {"cost": Decimal("NaN"), "currency": "CNY"},
            {"cost": Decimal("1"), "currency": None},
            {"started_at": MOMENT.replace(tzinfo=None)},
            {"finished_at": MOMENT.replace(hour=0)},
            {"input_tokens": -1},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(call_record(), **change)
```

- [x] **Step 2：执行测试命令，验证失败原因是尚无 `content_intelligence` 包，不是测试未被发现。**
- [x] **Step 3：创建空包入口并实现模型。**

`models.py` 的模型和校验完整定义如下：

```python
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite
from urllib.parse import urlsplit


def require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("Timestamp must have a timezone")


@dataclass(frozen=True)
class Topic:
    topic_id: str
    name: str
    keywords: tuple[str, ...]
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.topic_id.strip() or not self.name.strip():
            raise ValueError("Topic ID and name must not be empty")
        if not isinstance(self.keywords, tuple):
            raise ValueError("Keywords must be an immutable tuple")
        if not self.keywords or any(not keyword.strip() for keyword in self.keywords):
            raise ValueError("Keywords must not be empty")


@dataclass(frozen=True)
class CallRecord:
    call_id: str
    provider: str
    started_at: datetime
    finished_at: datetime
    model: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: Decimal | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if not self.call_id.strip() or not self.provider.strip():
            raise ValueError("Call ID and provider must not be empty")
        require_aware(self.started_at)
        require_aware(self.finished_at)
        started_at = self.started_at.astimezone(timezone.utc)
        finished_at = self.finished_at.astimezone(timezone.utc)
        if finished_at < started_at:
            raise ValueError("Call finish precedes start")
        for count in (self.input_tokens, self.output_tokens):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("Token count must be a non-negative integer")
        if (self.cost is None) != (self.currency is None):
            raise ValueError("Cost and currency must be supplied together")
        if self.cost is not None:
            if not self.cost.is_finite() or self.cost < 0:
                raise ValueError("Cost must be finite and non-negative")
            if not self.currency or len(self.currency) != 3 or not self.currency.isalpha():
                raise ValueError("Currency must be a three-letter code")

    @property
    def latency_ms(self) -> int:
        elapsed = self.finished_at.astimezone(timezone.utc) - self.started_at.astimezone(timezone.utc)
        return elapsed // timedelta(milliseconds=1)


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    summary: str
    relevance: float
    source_time: datetime | None = None

    def __post_init__(self) -> None:
        try:
            address = urlsplit(self.url)
            hostname = address.hostname
        except ValueError:
            raise ValueError("Source URL must be a valid HTTP(S) address") from None
        if address.scheme not in ("http", "https") or not hostname:
            raise ValueError("Source URL must use HTTP(S) with a host")
        if address.username is not None or address.password is not None:
            raise ValueError("Source URL must not include credentials")
        if not self.title.strip() or not self.summary.strip():
            raise ValueError("Source title and summary must not be empty")
        if isinstance(self.relevance, bool) or not isfinite(self.relevance):
            raise ValueError("Relevance must be finite")
        if self.source_time is not None:
            require_aware(self.source_time)


@dataclass(frozen=True)
class SearchBatch:
    results: tuple[SearchResult, ...]
    call: CallRecord

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple):
            raise ValueError("Search results must be an immutable tuple")


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    topic_id: str
    scan_date: date
    rank: int
    source: SearchResult
    call_id: str


@dataclass(frozen=True)
class RecommendationBatch:
    topic_id: str
    scan_date: date
    recommendations: tuple[Recommendation, ...]
    call: CallRecord | None
```

- [x] **Step 4：运行模型测试，确认通过；为缺失主题关键词、URL 中含凭据、缺失来源时间补充对应边界断言。**

```python
def test_source_time_can_be_unknown(self):
    self.assertIsNone(replace(source(1), source_time=None).source_time)

def test_rejects_empty_keywords_and_url_credentials(self):
    with self.assertRaises(ValueError):
        Topic("topic-1", "人工智能", (), True)
    with self.assertRaises(ValueError):
        replace(source(1), url="https://user:password@example.test/article")
```

将这两个方法加入 `ModelTests`，重新运行测试。每项失败修复只涉及本任务文件。

## Task 2：搜索端口和确定性测试适配器

**Files:** 创建 `ports.py`、`adapters/testing.py`；修改 `test_recommendations.py`。

**Interfaces:** 消费 Task 1 的 `SearchBatch`；产出 `SearchProvider.search(query, limit)` 和 `FakeSearchProvider`；错误只携带固定错误码、可重试标记和安全审计记录。

- [x] **Step 1：追加适配器测试。**

在测试文件中加入导入：

```python
from content_intelligence.models import SearchBatch
from content_intelligence.ports import ProviderError
from content_intelligence.adapters.testing import FakeSearchProvider
```

再加入以下测试类：

```python
class AdapterTests(unittest.TestCase):
    def test_fixed_input_returns_fixed_batch_and_records_requests(self):
        batch = SearchBatch((source(1),), call_record())
        provider = FakeSearchProvider(batch)
        self.assertEqual(provider.search("人工智能", 10), batch)
        self.assertEqual(provider.search("人工智能", 10), batch)
        self.assertEqual(provider.calls, [("人工智能", 10), ("人工智能", 10)])

    def test_failure_preserves_safe_audit_and_has_no_provider_payload(self):
        error = ProviderError(True, call_record())
        provider = FakeSearchProvider(error)
        with self.assertRaises(ProviderError) as caught:
            provider.search("人工智能", 10)
        self.assertEqual(str(caught.exception), "SEARCH_UNAVAILABLE")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.call, call_record())
```

- [x] **Step 2：运行测试，确认失败来自尚不存在的端口/适配器。**
- [x] **Step 3：实现以下内容。**

`ports.py`：

```python
from typing import Protocol
from .models import CallRecord, SearchBatch


class ProviderError(RuntimeError):
    def __init__(self, retryable: bool, call: CallRecord) -> None:
        super().__init__("SEARCH_UNAVAILABLE")
        self.retryable = retryable
        self.call = call


class SearchProvider(Protocol):
    def search(self, query: str, limit: int) -> SearchBatch: ...
```

`adapters/testing.py`：

```python
from ..models import SearchBatch
from ..ports import ProviderError


class FakeSearchProvider:
    def __init__(self, outcome: SearchBatch | ProviderError) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int) -> SearchBatch:
        self.calls.append((query, limit))
        if isinstance(self.outcome, ProviderError):
            raise ProviderError(self.outcome.retryable, self.outcome.call)
        return self.outcome
```

适配器刻意不截断 fixture，允许验证领域服务在供应商返回超量结果时仍守住上限。真实 SDK 异常和密钥没有进入端口模型；真实适配器的异常清洗在接入供应商时单独测试。

- [x] **Step 4：重新运行测试；确认无网络调用、不读取 API Key、不引入全局时钟和随机数。**

## Task 3：推荐算法及完整离线回归

**Files:** 创建 `recommendation_service.py`、模块 README；修改 `test_recommendations.py`。

**Interfaces:** 消费 `Topic`、`SearchProvider`、`SearchBatch`；产出 `RecommendationBatch`。禁用主题返回无调用批次；启用主题即使搜索为空也返回审计；供应商错误向调用者传播，不修改工作流状态。

- [x] **Step 1：追加服务测试。**

加入导入：

```python
from content_intelligence.recommendation_service import RecommendationService
```

再加入以下测试类：

```python
class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.topic = Topic("topic-1", "人工智能", ("人工智能", "课程"), True)
        self.day = date(2026, 9, 22)

    def test_caps_at_ten_deduplicates_and_orders_by_relevance(self):
        items = tuple(source(index, float(index)) for index in range(15))
        batch = SearchBatch(items + (replace(items[-1], relevance=99.0),), call_record())
        provider = FakeSearchProvider(batch)
        result = RecommendationService(provider).recommend(self.topic, self.day)
        self.assertEqual(len(result.recommendations), 10)
        self.assertEqual(result.recommendations[0].source.relevance, 99.0)
        scores = [item.source.relevance for item in result.recommendations]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len({item.source.url for item in result.recommendations}), 10)
        self.assertEqual([item.rank for item in result.recommendations], list(range(1, 11)))
        self.assertEqual(provider.calls, [("人工智能 课程", 10)])
        self.assertEqual(result.call, call_record())
        self.assertTrue(all(item.call_id == "call-1" for item in result.recommendations))

    def test_disabled_topic_does_not_call_provider(self):
        provider = FakeSearchProvider(SearchBatch((source(1),), call_record()))
        result = RecommendationService(provider).recommend(replace(self.topic, enabled=False), self.day)
        self.assertEqual(result.recommendations, ())
        self.assertIsNone(result.call)
        self.assertEqual(provider.calls, [])

    def test_empty_search_is_auditable(self):
        provider = FakeSearchProvider(SearchBatch((), call_record()))
        result = RecommendationService(provider).recommend(self.topic, self.day)
        self.assertEqual(result.recommendations, ())
        self.assertEqual(result.call, call_record())

    def test_ties_and_ids_are_stable_and_input_is_not_mutated(self):
        items = (source(2), source(1))
        provider = FakeSearchProvider(SearchBatch(items, call_record()))
        service = RecommendationService(provider)
        first = service.recommend(self.topic, self.day)
        second = service.recommend(self.topic, self.day)
        reversed_provider = FakeSearchProvider(SearchBatch(tuple(reversed(items)), call_record()))
        reordered = RecommendationService(reversed_provider).recommend(self.topic, self.day)
        self.assertEqual(first, second)
        self.assertEqual(first, reordered)
        self.assertEqual(provider.outcome.results, items)
        next_day = service.recommend(self.topic, date(2026, 9, 23))
        self.assertNotEqual(first.recommendations[0].recommendation_id, next_day.recommendations[0].recommendation_id)
        other_topic = service.recommend(replace(self.topic, topic_id="topic-2"), self.day)
        self.assertNotEqual(first.recommendations[0].recommendation_id, other_topic.recommendations[0].recommendation_id)

    def test_provider_failure_is_not_reported_as_empty_success(self):
        provider = FakeSearchProvider(ProviderError(True, call_record()))
        with self.assertRaises(ProviderError):
            RecommendationService(provider).recommend(self.topic, self.day)
```

- [x] **Step 2：运行测试，确认服务导入失败。**
- [x] **Step 3：实现推荐服务。**

```python
import json
from datetime import date
from uuid import NAMESPACE_URL, uuid5

from .models import Recommendation, RecommendationBatch, SearchResult, Topic
from .ports import SearchProvider


class RecommendationService:
    def __init__(self, provider: SearchProvider) -> None:
        self.provider = provider

    def recommend(self, topic: Topic, scan_date: date) -> RecommendationBatch:
        if not topic.enabled:
            return RecommendationBatch(topic.topic_id, scan_date, (), None)
        batch = self.provider.search(" ".join(topic.keywords), limit=10)
        ordered = sorted(
            batch.results,
            key=lambda item: (
                -item.relevance,
                item.url,
                item.title,
                item.summary,
                item.source_time.isoformat() if item.source_time is not None else "",
            ),
        )
        unique: dict[str, SearchResult] = {}
        for item in ordered:
            if item.url not in unique:
                unique[item.url] = item
            if len(unique) == 10:
                break
        recommendations = []
        for rank, item in enumerate(unique.values(), start=1):
            identity = json.dumps(
                ["live-make-recommendation", topic.topic_id, scan_date.isoformat(), item.url],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            recommendations.append(
                Recommendation(
                    str(uuid5(NAMESPACE_URL, identity)),
                    topic.topic_id,
                    scan_date,
                    rank,
                    item,
                    batch.call.call_id,
                )
            )
        return RecommendationBatch(topic.topic_id, scan_date, tuple(recommendations), batch.call)
```

- [x] **Step 4：运行全部内容测试，再运行共享契约测试，检查用例数量和退出码。**
- [x] **Step 5：写模块 README，使用如下内容并附上本计划的完整 PowerShell 测试命令。**

```markdown
# Content Intelligence — 离线推荐切片

当前实现：不可变来源及调用模型、搜索 Provider 端口、固定结果测试适配器、推荐去重/排序/最多 10 条及调用审计关联。

要求：Python 3.11；无需第三方依赖、网络、数据库、Docker 或 API Key。

所有 ID、时间和结果均以输入为准；测试适配器并非真实博查接入。

服务返回 RecommendationBatch，不写数据库，不发送事件，不修改任务状态。
推荐替换和结果落库由业务 API 负责，执行时间和重试由工作流负责。

本切片尚不包含真实博查/LLM、脚本生成、安全审核、审批、媒体与发布，不能据此关闭 Issue #12。
```

- [x] **Step 6：检查 `git diff --check` 和 `git status --short`；只能出现本计划明确列出的内容模块、测试和文档文件。**

不运行 `git commit`、`git push` 或合并命令。最终报告实际测试数量、修改文件和下一批边界。

## 第一批之后的对接顺序

1. 与 #10、#11 确认推荐和脚本结果的提交/持久化接口、事件幂等及投递责任；当前 `topic.discovered.v1` 只有推荐 ID，不含推荐详情。
2. 确认团队完整风险规则与版本、汉字计数口径、候选差异阈值、事实证据格式；不能把简单关键词匹配或两个 URL 等同于事实核验。
3. 脚本与审核切片：测试 LLM 适配器、三份候选、最多两轮、审核结果、HIGH 双审和 BLOCKED 不可审批；仍不修改状态。
4. 供应商与集成切片：核对真实博查/LLM 官方接口，实现凭据注入、超时、错误清洗、用量审计及少量手动冒烟验证；之后接入结果持久化和事件发布。

以上是后续切片的顺序和前置条件，不是宣称已经实现，也不允许第一批实现者凭空发明共享接口。
