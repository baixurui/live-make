from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
import traceback
import unittest

from content_intelligence.models import CallRecord, SearchBatch, SearchResult, Topic
from content_intelligence.ports import ProviderError
from content_intelligence.adapters.testing import FakeSearchProvider
from content_intelligence.recommendation_service import RecommendationService


MOMENT = datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)


class FixtureTransitionTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta:
        if value is None:
            return timedelta(hours=-5)
        if value.month == 3:
            return timedelta(hours=-5 if value.hour < 3 else -4)
        return timedelta(hours=-5 if value.fold else -4)

    def dst(self, value: datetime | None) -> timedelta:
        return self.utcoffset(value) - timedelta(hours=-5)


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
    def test_models_are_immutable_and_unknown_usage_is_not_zero(self) -> None:
        topic = Topic("topic-1", "人工智能", ("人工智能",), True)
        with self.assertRaises(FrozenInstanceError):
            topic.enabled = False
        audit = call_record()
        self.assertIsNone(audit.cost)
        self.assertIsNone(audit.currency)
        self.assertIsNone(audit.model)
        self.assertIsNone(audit.prompt_version)
        self.assertIsNone(audit.input_tokens)
        self.assertIsNone(audit.output_tokens)

    def test_rejects_invalid_relevance(self) -> None:
        for score in (float("nan"), float("inf"), -float("inf"), True):
            with self.subTest(score=score), self.assertRaises(ValueError):
                source(1, score)

    def test_rejects_invalid_urls_without_echoing_credentials(self) -> None:
        for url in (
            "file:///tmp/source",
            "https:///missing-host",
            "not-a-url",
            "https://user:secret-sentinel@example.test/article",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError) as caught:
                replace(source(1), url=url)
            self.assertNotIn("secret-sentinel", str(caught.exception))

    def test_parser_failures_do_not_expose_credentials_in_tracebacks(self) -> None:
        for url in (
            "https://user:secret-sentinel@example.test\uff0farticle",
            "https://[secret-sentinel]/article",
        ):
            with self.subTest(url=url):
                try:
                    replace(source(1), url=url)
                except ValueError as error:
                    self.assertNotIn("secret-sentinel", str(error))
                    self.assertNotIn("secret-sentinel", "".join(traceback.format_exception(error)))
                else:
                    self.fail("Malformed source URL was accepted")

    def test_source_time_can_be_unknown_but_not_naive(self) -> None:
        self.assertIsNone(replace(source(1), source_time=None).source_time)
        with self.assertRaises(ValueError):
            replace(source(1), source_time=MOMENT.replace(tzinfo=None))

    def test_rejects_blank_source_title_or_summary(self) -> None:
        for change in ({"title": " "}, {"summary": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(source(1), **change)

    def test_topic_requires_identity_and_immutable_nonblank_keywords(self) -> None:
        topic = Topic("topic-1", "人工智能", ("人工智能",), True)
        for change in (
            {"topic_id": " "},
            {"name": ""},
            {"keywords": ()},
            {"keywords": ("人工智能", " ")},
            {"keywords": ["人工智能"]},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(topic, **change)

    def test_rejects_invalid_audit_identity_times_usage_or_cost(self) -> None:
        for change in (
            {"call_id": " "},
            {"provider": ""},
            {"cost": Decimal("-1"), "currency": "CNY"},
            {"cost": Decimal("NaN"), "currency": "CNY"},
            {"cost": Decimal("Infinity"), "currency": "CNY"},
            {"cost": Decimal("1"), "currency": None},
            {"cost": None, "currency": "CNY"},
            {"cost": Decimal("1"), "currency": ""},
            {"cost": Decimal("1"), "currency": "123"},
            {"started_at": MOMENT.replace(tzinfo=None)},
            {"finished_at": MOMENT.replace(tzinfo=None)},
            {"finished_at": MOMENT.replace(hour=0)},
            {"input_tokens": -1},
            {"output_tokens": -1},
            {"input_tokens": True},
            {"output_tokens": 1.5},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(call_record(), **change)

    def test_audit_keeps_exact_cost_usage_and_elapsed_milliseconds(self) -> None:
        audit = replace(
            call_record(),
            finished_at=MOMENT + timedelta(milliseconds=125),
            model="fixture-model",
            prompt_version="fixture-v1",
            input_tokens=3,
            output_tokens=4,
            cost=Decimal("0.0125"),
            currency="CNY",
        )
        self.assertEqual(audit.latency_ms, 125)
        self.assertEqual(audit.cost, Decimal("0.0125"))
        self.assertEqual((audit.input_tokens, audit.output_tokens), (3, 4))

    def test_audit_does_not_lose_milliseconds_to_float_rounding(self) -> None:
        audit = replace(call_record(), finished_at=MOMENT + timedelta(milliseconds=1001))
        self.assertEqual(audit.latency_ms, 1001)

    def test_audit_uses_elapsed_time_across_fall_clock_rollback(self) -> None:
        zone = FixtureTransitionTimezone()
        start = datetime(2026, 11, 1, 1, 59, 59, tzinfo=zone, fold=0)
        finish = datetime(2026, 11, 1, 1, 0, 1, tzinfo=zone, fold=1)
        audit = CallRecord("call-1", "fake-search", start, finish)
        self.assertEqual(audit.latency_ms, 2000)
        self.assertIs(audit.started_at, start)
        self.assertIs(audit.finished_at, finish)

    def test_audit_uses_elapsed_time_across_spring_clock_jump(self) -> None:
        zone = FixtureTransitionTimezone()
        start = datetime(2026, 3, 8, 1, 59, 59, tzinfo=zone)
        finish = datetime(2026, 3, 8, 3, 0, 1, tzinfo=zone)
        audit = CallRecord("call-1", "fake-search", start, finish)
        self.assertEqual(audit.latency_ms, 2000)

    def test_audit_rejects_reverse_actual_time_despite_later_wall_clock(self) -> None:
        zone = FixtureTransitionTimezone()
        start = datetime(2026, 11, 1, 1, 0, 1, tzinfo=zone, fold=1)
        finish = datetime(2026, 11, 1, 1, 59, 59, tzinfo=zone, fold=0)
        with self.assertRaises(ValueError):
            CallRecord("call-1", "fake-search", start, finish)

    def test_batch_requires_immutable_results_and_retains_empty_call(self) -> None:
        batch = SearchBatch((), call_record())
        self.assertEqual(batch.results, ())
        self.assertEqual(batch.call, call_record())
        with self.assertRaises(ValueError):
            SearchBatch([source(1)], call_record())


class AdapterTests(unittest.TestCase):
    def test_fixed_input_returns_fixed_batch_and_records_requests(self) -> None:
        batch = SearchBatch((source(1),), call_record())
        provider = FakeSearchProvider(batch)
        self.assertEqual(provider.search("人工智能", 10), batch)
        self.assertEqual(provider.search("人工智能", 10), batch)
        self.assertEqual(provider.calls, [("人工智能", 10), ("人工智能", 10)])

    def test_fixture_is_not_truncated_before_domain_validation(self) -> None:
        batch = SearchBatch(tuple(source(index) for index in range(15)), call_record())
        provider = FakeSearchProvider(batch)
        self.assertEqual(len(provider.search("人工智能", 10).results), 15)
        self.assertEqual(provider.outcome, batch)

    def test_empty_fixture_retains_call_audit(self) -> None:
        batch = SearchBatch((), call_record())
        self.assertEqual(FakeSearchProvider(batch).search("人工智能", 10), batch)

    def test_failure_preserves_safe_audit_without_provider_payload(self) -> None:
        for retryable in (True, False):
            with self.subTest(retryable=retryable):
                error = ProviderError(retryable, call_record())
                provider = FakeSearchProvider(error)
                for attempt in range(2):
                    with self.subTest(attempt=attempt), self.assertRaises(ProviderError) as caught:
                        provider.search("人工智能", 10)
                    self.assertEqual(str(caught.exception), "SEARCH_UNAVAILABLE")
                    self.assertEqual(caught.exception.retryable, retryable)
                    self.assertEqual(caught.exception.call, call_record())
                    self.assertIsNot(caught.exception, error)
                    self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(len(provider.calls), 2)


class RecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topic = Topic("topic-1", "人工智能", ("人工智能", "课程"), True)
        self.day = date(2026, 9, 22)

    def test_caps_at_ten_deduplicates_and_orders_by_relevance(self) -> None:
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
        self.assertEqual(result.topic_id, self.topic.topic_id)
        self.assertEqual(result.scan_date, self.day)
        for recommendation in result.recommendations:
            self.assertEqual(recommendation.call_id, "call-1")
            self.assertEqual(recommendation.topic_id, self.topic.topic_id)
            self.assertEqual(recommendation.scan_date, self.day)
            self.assertIn(recommendation.source, batch.results)

    def test_returns_all_unique_sources_below_cap_and_caps_only_above_it(self) -> None:
        for size in (1, 9, 10, 11):
            with self.subTest(size=size):
                batch = SearchBatch(tuple(source(index) for index in range(size)), call_record())
                result = RecommendationService(FakeSearchProvider(batch)).recommend(self.topic, self.day)
                self.assertEqual(len(result.recommendations), min(size, 10))

    def test_disabled_topic_does_not_call_provider(self) -> None:
        provider = FakeSearchProvider(SearchBatch((source(1),), call_record()))
        result = RecommendationService(provider).recommend(replace(self.topic, enabled=False), self.day)
        self.assertEqual(result.recommendations, ())
        self.assertIsNone(result.call)
        self.assertEqual(provider.calls, [])

    def test_empty_search_is_auditable(self) -> None:
        provider = FakeSearchProvider(SearchBatch((), call_record()))
        result = RecommendationService(provider).recommend(self.topic, self.day)
        self.assertEqual(result.recommendations, ())
        self.assertEqual(result.call, call_record())
        self.assertEqual(provider.calls, [("人工智能 课程", 10)])

    def test_ties_and_ids_are_stable_and_input_is_not_mutated(self) -> None:
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
        self.assertEqual(first.recommendations[0].source.url, source(1).url)
        next_day = service.recommend(self.topic, date(2026, 9, 23))
        self.assertNotEqual(
            first.recommendations[0].recommendation_id,
            next_day.recommendations[0].recommendation_id,
        )
        other_topic = service.recommend(replace(self.topic, topic_id="topic-2"), self.day)
        self.assertNotEqual(
            first.recommendations[0].recommendation_id,
            other_topic.recommendations[0].recommendation_id,
        )

    def test_duplicate_ties_have_deterministic_selection(self) -> None:
        items = (
            source(1),
            replace(source(1), source_time=None),
            replace(source(1), title="other-title"),
            replace(source(1), summary="other-summary"),
        )
        first = RecommendationService(FakeSearchProvider(SearchBatch(items, call_record())))
        reversed_service = RecommendationService(
            FakeSearchProvider(SearchBatch(tuple(reversed(items)), call_record()))
        )
        result = first.recommend(self.topic, self.day)
        self.assertEqual(len(result.recommendations), 1)
        self.assertEqual(result, reversed_service.recommend(self.topic, self.day))

    def test_query_parameters_are_not_merged_as_duplicate_urls(self) -> None:
        items = (
            replace(source(1), url="https://example.test/article?id=1"),
            replace(source(1), url="https://example.test/article?id=2"),
        )
        service = RecommendationService(FakeSearchProvider(SearchBatch(items, call_record())))
        result = service.recommend(self.topic, self.day)
        self.assertEqual(len(result.recommendations), 2)
        self.assertNotEqual(
            result.recommendations[0].recommendation_id,
            result.recommendations[1].recommendation_id,
        )

    def test_provider_failure_is_not_reported_as_empty_success_or_retried(self) -> None:
        provider = FakeSearchProvider(ProviderError(True, call_record()))
        with self.assertRaises(ProviderError) as caught:
            RecommendationService(provider).recommend(self.topic, self.day)
        self.assertEqual(caught.exception.call, call_record())
        self.assertEqual(provider.calls, [("人工智能 课程", 10)])


if __name__ == "__main__":
    unittest.main()
