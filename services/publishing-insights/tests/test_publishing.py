from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
import yaml

MODULE = Path(__file__).resolve().parents[1]
ROOT = MODULE.parents[1]
sys.path.insert(0, str(MODULE))

from publishing.adapters import HttpTaskSource, SimulatedPublisher, simulate_metrics
from publishing.api import Settings, create_app
from publishing.insights import Insights
from publishing.models import (DependencyUnavailable, DomainError, PublishRequest, PublishResult,
                               TaskContext, WEEK, stamp)
from publishing.service import PublishingService
from publishing.storage import Store


START = datetime(2026, 9, 22, 4, tzinfo=timezone.utc)
WORKFLOW = "workflow-test-token-only"
METRICS = "metrics-test-token-only"
FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=ValueError)
def rfc3339(value):
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", value):
        return False
    return datetime.fromisoformat(value.upper().replace("Z", "+00:00")).tzinfo is not None


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


class Source:
    def __init__(self):
        self.records = {}
        self.unavailable = False

    def get(self, task_id):
        if self.unavailable:
            raise DependencyUnavailable("offline")
        return self.records[task_id]


class CountingPublisher:
    def __init__(self, results=(True,)):
        self.results = results
        self.calls = []
        self.hook = None

    def publish(self, key, attempt, seed):
        self.calls.append((key, attempt, seed))
        if self.hook:
            self.hook(attempt)
        ok = self.results[min(attempt - 1, len(self.results) - 1)]
        return PublishResult(success=ok, platform_post_id=f"replacement-{key}" if ok else None,
                             error_code=None if ok else "SIMULATED_PLATFORM_FAILURE")


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock, self.source = Clock(), Source()
        self.store = Store(Path(self.temp.name) / "publishing.sqlite3")
        self.publisher = CountingPublisher()
        self.service = PublishingService(self.store, self.source, self.publisher, "test-seed", self.clock)
        self.insights = Insights(self.store, self.clock)

    def request(self, key="key-1", account="account-1", scheduled=START):
        task = f"task-{key}"
        self.source.records[task] = TaskContext(
            task_id=task, account_id=account, status="SCHEDULED", scheduled_at=scheduled,
            media_version="media-1", approved_media_version="media-1", script_approved=True,
            video_approved=True, qc_passed=True, risk_level="LOW", qc_score=80.0, review_score=70.0,
        )
        return PublishRequest(
            event_id=f"event-{key}", event_type="task.publish_requested.v1", occurred_at=scheduled,
            correlation_id=f"correlation-{key}", idempotency_key=key,
            data={"task_id": task, "receipt_id": f"receipt-{key}", "scheduled_at": scheduled},
        )

    def replace_context(self, request, **updates):
        task = request.data.task_id
        self.source.records[task] = self.source.records[task].model_copy(update=updates)

    def app(self, worker=False):
        settings = Settings(str(self.store.path), WORKFLOW, {METRICS: frozenset({"account-1"})},
                            worker_interval=0.01)
        return create_app(settings, source=self.source, publisher=self.publisher, clock=self.clock,
                          worker_enabled=worker)

    def rows(self, table):
        with self.store.connection() as connection:
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]

    def test_eligibility_rejects_invalid_business_state_without_publishing(self):
        cases = [
            ({"status": "VIDEO_APPROVED"}, "TASK_NOT_SCHEDULED"),
            ({"status": "PAUSED"}, "TASK_NOT_SCHEDULED"),
            ({"status": "CANCELLED"}, "TASK_NOT_SCHEDULED"),
            ({"script_approved": False}, "APPROVAL_REQUIRED"),
            ({"video_approved": False}, "APPROVAL_REQUIRED"),
            ({"qc_passed": False}, "APPROVAL_REQUIRED"),
            ({"risk_level": "BLOCKED"}, "RISK_BLOCKED"),
            ({"approved_media_version": "old"}, "APPROVED_VERSION_MISMATCH"),
            ({"task_id": "another"}, "TASK_MISMATCH"),
            ({"scheduled_at": START + timedelta(days=1)}, "SCHEDULE_MISMATCH"),
        ]
        for changes, code in cases:
            with self.subTest(code=code, changes=changes):
                request = self.request()
                self.replace_context(request, **changes)
                with self.assertRaisesRegex(DomainError, code):
                    self.service.submit(request)
        self.assertEqual(self.publisher.calls, [])
        self.assertEqual(self.rows("publications"), [])

    def test_schedule_and_optional_claims(self):
        request = self.request()
        for now, code in [(START - timedelta(seconds=1), "NOT_DUE"),
                          (START + timedelta(hours=12), "SCHEDULE_EXPIRED")]:
            self.clock.now = now
            with self.assertRaisesRegex(DomainError, code):
                self.service.submit(request)
        self.clock.now = START
        for changes, code in [({"account_id": "fake"}, "ACCOUNT_MISMATCH"),
                              ({"media_version": "old"}, "MEDIA_VERSION_MISMATCH")]:
            altered = request.model_copy(update={"data": request.data.model_copy(update=changes)})
            with self.assertRaisesRegex(DomainError, code):
                self.service.submit(altered)
        noon_wrong = self.request("wrong-time", scheduled=START - timedelta(minutes=1))
        with self.assertRaisesRegex(DomainError, "INVALID_SCHEDULE"):
            self.service.submit(noon_wrong)
        self.clock.now = START + timedelta(hours=1)
        self.replace_context(request, risk_level="HIGH", status="PUBLISHING")
        self.assertEqual(self.service.submit(request).status, "SUCCEEDED")

    def test_identical_concurrent_requests_and_restart_have_one_result(self):
        request = self.request()
        services = [PublishingService(Store(self.store.path), self.source, self.publisher, "test-seed", self.clock)
                    for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(lambda service: service.submit(request), services))
        self.assertTrue(all(receipt == receipts[0] for receipt in receipts))
        self.assertEqual(len(self.publisher.calls), 1)
        self.clock.now += timedelta(days=30)
        self.source.unavailable = True
        replay = request.model_copy(update={"event_id": "redelivered", "occurred_at": self.clock.now})
        restarted = PublishingService(Store(self.store.path), self.source, self.publisher, "new-seed", self.clock)
        self.assertEqual(restarted.submit(replay), receipts[0])
        self.assertEqual(len(self.rows("outbox")), 1)

    def test_same_key_conflict_and_task_rekey_are_rejected(self):
        request = self.request()
        original = self.service.submit(request)
        for changes in ({"receipt_id": "other"}, {"task_id": "other"},
                        {"account_id": "other"}, {"media_version": "other"},
                        {"scheduled_at": START + timedelta(days=1)}):
            altered = request.model_copy(update={"data": request.data.model_copy(update=changes)})
            with self.assertRaisesRegex(DomainError, "IDEMPOTENCY_CONFLICT"):
                self.service.submit(altered)
        with self.assertRaisesRegex(DomainError, "PUBLICATION_ALREADY_RESERVED"):
            self.service.submit(request.model_copy(update={"idempotency_key": "another-key"}))
        self.assertEqual(self.service.receipt(original.receipt_id), original)
        self.assertEqual(len(self.publisher.calls), 1)

    def test_account_day_race_allows_only_one_task(self):
        requests = [self.request(str(i)) for i in range(8)]
        def submit(request):
            try:
                return self.service.submit(request).status
            except DomainError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, requests))
        self.assertEqual(results.count("SUCCEEDED"), 1)
        self.assertEqual(results.count("PUBLICATION_ALREADY_RESERVED"), 7)
        self.assertEqual(len(self.publisher.calls), 1)

    def test_single_retry_and_final_failure_event(self):
        self.publisher.results = (False, False)
        request = self.request()
        receipt = self.service.submit(request)
        self.assertEqual(receipt.status, "FAILED")
        self.assertEqual([a.number for a in receipt.attempts], [1, 2])
        event = self.service.events()[0]
        self.assertEqual(event["data"]["attempt"], 2)
        self.assertFalse(event["data"]["retryable"])
        self.assertEqual(event["data"]["failed_step"], "publishing")
        self.assertEqual(self.service.submit(request), receipt)
        self.assertEqual(len(self.publisher.calls), 2)
        self.assertEqual(self.source.records[request.data.task_id].status, "SCHEDULED")

    def test_restart_after_first_failure_preserves_budget_and_seed(self):
        request = self.request()
        self.publisher.results = (False, True)
        self.publisher.hook = lambda attempt: setattr(self.source, "unavailable", True)
        receipt = self.service.submit(request)
        self.assertEqual(receipt.status, "PROCESSING")
        self.assertEqual(len(receipt.attempts), 1)
        self.assertEqual(self.service.events(), [])
        self.source.unavailable = False
        self.publisher.hook = None
        restarted = PublishingService(Store(self.store.path), self.source, self.publisher, "changed", self.clock)
        restarted.recover()
        receipt = restarted.receipt(request.data.receipt_id)
        self.assertEqual(receipt.status, "SUCCEEDED")
        self.assertEqual(self.publisher.calls, [("key-1", 1, "test-seed"), ("key-1", 2, "test-seed")])

    def test_revoked_approval_before_retry_aborts_without_second_call(self):
        request = self.request()
        self.publisher.results = (False, True)
        self.publisher.hook = lambda attempt: self.replace_context(request, video_approved=False)
        receipt = self.service.submit(request)
        self.assertEqual(receipt.status, "ABORTED")
        self.assertEqual(receipt.error_code, "APPROVAL_REQUIRED")
        self.assertEqual(len(self.publisher.calls), 1)
        self.assertFalse(self.service.events()[0]["data"]["retryable"])

    def test_context_change_between_acceptance_and_attempt_is_rejected(self):
        request = self.request()
        initial = self.source.get(request.data.task_id)
        changed = initial.model_copy(update={"media_version": "new", "approved_media_version": "new"})
        with patch.object(self.source, "get", side_effect=[initial, changed]):
            receipt = self.service.submit(request)
        self.assertEqual(receipt.status, "ABORTED")
        self.assertEqual(receipt.error_code, "BUSINESS_CONTEXT_CHANGED")
        self.assertEqual(self.publisher.calls, [])

    def test_unavailable_business_state_does_not_publish(self):
        request = self.request()
        self.source.unavailable = True
        with self.assertRaises(DependencyUnavailable):
            self.service.submit(request)
        self.assertEqual(self.rows("publications"), [])
        self.assertEqual(self.publisher.calls, [])

    def test_outbox_survives_restart_and_ack_is_idempotent(self):
        request = self.request()
        self.service.submit(request)
        events = self.service.events()
        restarted = PublishingService(Store(self.store.path), self.source, self.publisher, "seed", self.clock)
        self.assertEqual(restarted.events(), events)
        restarted.acknowledge(events[0]["event_id"])
        restarted.acknowledge(events[0]["event_id"])
        self.assertEqual(self.service.events(), [])
        self.assertEqual(len(self.publisher.calls), 1)
        with self.assertRaisesRegex(DomainError, "EVENT_NOT_FOUND"):
            restarted.acknowledge("absent")

    def test_outbox_write_failure_rolls_back_final_result(self):
        request = self.request()
        with self.store.transaction() as connection:
            connection.execute("""CREATE TRIGGER reject_outbox BEFORE INSERT ON outbox
                                  BEGIN SELECT RAISE(ABORT, 'injected outage'); END""")
        with self.assertRaises(Exception):
            self.service.submit(request)
        self.assertEqual(self.service.receipt(request.data.receipt_id).status, "PROCESSING")
        self.assertEqual(self.rows("attempts"), [])
        self.assertEqual(self.rows("outbox"), [])
        with self.store.transaction() as connection:
            connection.execute("DROP TRIGGER reject_outbox")
        self.service.recover()
        self.assertEqual(len(self.rows("outbox")), 1)
        self.assertEqual(self.service.receipt(request.data.receipt_id).platform_post_id, "replacement-key-1")

    def test_restart_after_both_failures_never_resets_attempts(self):
        self.publisher.results = (False, False)
        request = self.request()
        self.publisher.hook = lambda attempt: setattr(self.source, "unavailable", True)
        self.assertEqual(self.service.submit(request).status, "PROCESSING")
        self.source.unavailable = False
        self.publisher.hook = None
        restarted = PublishingService(Store(self.store.path), self.source, self.publisher, "new-seed", self.clock)
        restarted.recover()
        failed = restarted.receipt(request.data.receipt_id)
        self.assertEqual(failed.status, "FAILED")
        restarted.recover()
        self.assertEqual(restarted.submit(request), failed)
        self.assertEqual(len(self.publisher.calls), 2)

    def test_three_snapshot_stages_latest_aggregation_and_retention(self):
        request = self.request()
        self.service.submit(request)
        self.clock.now = START + timedelta(hours=1) - timedelta(microseconds=1)
        self.insights.maintain()
        self.assertEqual(self.rows("snapshots"), [])
        for duration, count in [(timedelta(hours=1), 1), (timedelta(days=1), 2), (WEEK, 3)]:
            self.clock.now = START + duration
            self.insights.maintain()
            self.insights.maintain()
            self.assertEqual(len(self.rows("snapshots")), count)
        values = [json.loads(row["metrics_json"]) for row in self.rows("snapshots")]
        for field in values[0]:
            self.assertTrue(0 <= values[0][field] <= values[1][field] <= values[2][field])
        result = self.insights.metrics("account-1")
        self.assertEqual(result.metrics.plays, values[-1]["plays"])
        self.assertEqual(result.snapshot_count, 1)
        self.assertEqual(result.published_count, 1)
        self.clock.now += timedelta(microseconds=1)
        self.assertEqual(self.insights.metrics("account-1").published_count, 0)
        self.clock.now = START + 2 * WEEK
        self.insights.maintain()
        self.assertEqual(self.rows("snapshots"), [])
        self.insights.maintain()
        self.assertEqual(self.rows("snapshots"), [])
        self.assertEqual(len(self.rows("snapshot_stages")), 3)
        self.assertEqual(self.service.submit(request).status, "SUCCEEDED")
        self.assertEqual(len(self.publisher.calls), 1)

    def test_late_catchup_missing_scores_and_cleanup_independent_of_query(self):
        request = self.request()
        self.replace_context(request, qc_score=None, review_score=None)
        self.service.submit(request)
        self.clock.now = START + WEEK
        Insights(Store(self.store.path), self.clock).maintain()
        snapshots = self.rows("snapshots")
        self.assertEqual(len(snapshots), 3)
        self.assertTrue(all(row["degraded"] == 1 for row in snapshots))
        self.assertEqual(len({row["generated_at"] for row in snapshots}), 1)
        self.assertEqual(len({row["sampled_at"] for row in snapshots}), 3)
        # An expired snapshot is excluded by the query even before physical cleanup.
        with self.store.transaction() as connection:
            connection.execute("UPDATE snapshots SET generated_at=?", (stamp(START),))
        self.assertEqual(self.insights.metrics("account-1").snapshot_count, 0)

    def test_two_snapshots_are_not_added(self):
        self.service.submit(self.request())
        self.clock.now = START + timedelta(days=1)
        self.insights.maintain()
        with self.store.transaction() as connection:
            for stage, plays in [(3600, 100), (86400, 300)]:
                connection.execute("UPDATE snapshots SET metrics_json=? WHERE stage=?",
                                   (json.dumps({"plays": plays, "likes": 0, "comments": 0,
                                                "favorites": 0, "shares": 0, "followers": 0}), stage))
        self.assertEqual(self.insights.metrics("account-1").metrics.plays, 300)

    def test_success_rate_retry_pending_aborted_and_empty(self):
        # Account-per-day uniqueness is respected: use separate scheduled days.
        for i, outcomes in enumerate([(True,), (False, True), (False, False), (False, True)]):
            scheduled = START + timedelta(days=i)
            self.clock.now = scheduled
            self.publisher.results = outcomes
            request = self.request(f"rate-{i}", scheduled=scheduled)
            if i == 3:
                self.publisher.hook = lambda attempt: setattr(self.source, "unavailable", True)
            self.service.submit(request)
        self.publisher.hook = None
        self.source.unavailable = False
        self.clock.now += timedelta(seconds=1)
        metrics = self.insights.metrics("account-1")
        self.assertEqual(metrics.outcomes.succeeded, 2)
        self.assertEqual(metrics.outcomes.failed, 1)
        self.assertEqual(metrics.outcomes.processing, 1)
        self.assertEqual(metrics.outcomes.success_rate, 2 / 3)
        self.assertEqual(metrics.snapshot_count, 0)
        self.assertEqual(metrics.published_count, 2)
        empty = self.insights.metrics("empty-account")
        self.assertEqual(empty.metrics.plays, 0)
        self.assertIsNone(empty.outcomes.success_rate)

    def test_window_end_exclusion_and_beijing_daily_group(self):
        self.service.submit(self.request())
        self.assertEqual(self.insights.metrics("account-1").published_count, 0)
        self.clock.now += timedelta(hours=13)
        metrics = self.insights.metrics("account-1")
        active = [daily for daily in metrics.daily_metrics if daily.published_count]
        self.assertEqual(active[0].date, "2026-09-22")
        self.assertEqual(metrics.daily_metrics[-1].date, "2026-09-23")
        self.assertEqual(len(metrics.daily_metrics), 8)

    def test_aborted_tasks_are_reported_outside_success_rate(self):
        request = self.request()
        self.publisher.results = (False, True)
        self.publisher.hook = lambda attempt: self.replace_context(request, video_approved=False)
        self.service.submit(request)
        self.clock.now += timedelta(seconds=1)
        outcomes = self.insights.metrics("account-1").outcomes
        self.assertEqual(outcomes.aborted, 1)
        self.assertEqual(outcomes.failed, 0)
        self.assertIsNone(outcomes.success_rate)

    def test_account_metrics_do_not_include_other_accounts(self):
        self.service.submit(self.request("one", account="account-1"))
        self.service.submit(self.request("two", account="account-2"))
        self.clock.now += timedelta(hours=1)
        self.insights.maintain()
        one, two = self.insights.metrics("account-1"), self.insights.metrics("account-2")
        self.assertEqual(one.published_count, 1)
        self.assertEqual(two.outcomes.succeeded, 1)
        snapshot = next(row for row in self.rows("snapshots") if row["receipt_id"] == "receipt-one")
        self.assertEqual(one.metrics.model_dump(), json.loads(snapshot["metrics_json"]))

    def test_http_auth_contract_errors_receipts_and_metrics_scope(self):
        request = self.request()
        with TestClient(self.app()) as client:
            body = request.model_dump(mode="json")
            path = "/api/v1/internal/publishing/requests"
            workflow = {"Authorization": f"Bearer {WORKFLOW}"}
            metrics = {"Authorization": f"Bearer {METRICS}"}
            for headers in ({}, metrics, {"Authorization": "Bearer invented"}):
                self.assertEqual(client.post(path, json=body, headers=headers).status_code, 401)
            for corrupt in ({**body, "event_type": "other"},
                            {**body, "occurred_at": "2026-09-22T12:00:00"},
                            {**body, "data": {**body["data"], "approved": True}},
                            {**body, "idempotency_key": ""}):
                self.assertEqual(client.post(path, json=corrupt, headers=workflow).status_code, 422)
            response = client.post(path, json=body, headers=workflow)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "SUCCEEDED")
            self.assertEqual(client.get("/api/v1/internal/publishing/receipts/receipt-key-1",
                                        headers=workflow).json(), response.json())
            self.assertEqual(client.get("/api/v1/internal/publishing/receipts/missing",
                                        headers=workflow).status_code, 404)
            self.clock.now += timedelta(days=1)
            self.insights.maintain()
            query = client.get("/api/v1/metrics?account_id=account-1", headers=metrics)
            self.assertEqual(query.status_code, 200, query.text)
            self.assertGreater(query.json()["metrics"]["plays"], 0)
            self.assertEqual(client.get("/api/v1/metrics?account_id=another", headers=metrics).status_code, 403)
            self.assertEqual(client.get("/api/v1/metrics?account_id=account-1", headers=workflow).status_code, 401)
            events = client.get("/api/v1/internal/publishing/events", headers=workflow)
            self.assertEqual(events.status_code, 200, events.text)
            event = events.json()[0]
            ack = client.post(f"/api/v1/internal/publishing/events/{event['event_id']}/ack", headers=workflow)
            self.assertEqual(ack.status_code, 204)
            self.assertEqual(client.get("/api/v1/internal/publishing/events", headers=workflow).json(), [])

    def test_http_dependency_unavailable_and_processing_response(self):
        request = self.request()
        headers = {"Authorization": f"Bearer {WORKFLOW}"}
        path = "/api/v1/internal/publishing/requests"
        with TestClient(self.app()) as client:
            self.source.unavailable = True
            self.assertEqual(client.post(path, json=request.model_dump(mode="json"), headers=headers).status_code, 503)
            self.source.unavailable = False
            self.publisher.results = (False, True)
            self.publisher.hook = lambda attempt: setattr(self.source, "unavailable", True)
            response = client.post(path, json=request.model_dump(mode="json"), headers=headers)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["status"], "PROCESSING")

    def test_background_worker_recovers_and_generates_snapshots(self):
        request = self.request()
        self.publisher.results = (False, True)
        self.publisher.hook = lambda attempt: setattr(self.source, "unavailable", True)
        self.service.submit(request)
        self.source.unavailable = False
        self.publisher.hook = None
        app = self.app(worker=True)
        done = threading.Event()
        original = app.state.insights.maintain
        def maintain():
            original()
            done.set()
        with patch.object(app.state.insights, "maintain", side_effect=maintain), TestClient(app):
            self.assertTrue(done.wait(5), "background recovery did not run")
        self.assertEqual(self.service.receipt(request.data.receipt_id).status, "SUCCEEDED")
        self.clock.now += timedelta(hours=1)
        done.clear()
        with patch.object(app.state.insights, "maintain", side_effect=maintain), TestClient(app):
            self.assertTrue(done.wait(5), "background snapshots did not run")
        self.assertEqual(len(self.rows("snapshots")), 1)

    def test_final_events_conform_to_shared_json_schemas(self):
        for success in (True, False):
            self.publisher.results = (success, success)
            request = self.request(str(success), account=str(success))
            self.service.submit(request)
        for event in self.service.events():
            schema = json.loads((ROOT / "contracts/events/v1" / f"{event['event_type']}.json").read_text())
            Draft202012Validator(schema, format_checker=FORMATS).validate(event)

    def test_exported_openapi_matches_runtime(self):
        expected = json.loads((ROOT / "contracts/publishing-insights.openapi.json").read_text())
        actual = self.app().openapi()
        # Provider-owned endpoint is documented alongside consumer-owned runtime routes.
        expected["paths"].pop("/api/v1/internal/tasks/{task_id}/publishing-context")
        expected["components"]["schemas"].pop("TaskContext")
        self.assertEqual(actual, expected)

    def test_openapi_references_and_runtime_response_validate(self):
        contract_path = ROOT / "contracts/publishing-insights.openapi.json"
        contract = json.loads(contract_path.read_text())
        root = yaml.safe_load((ROOT / "contracts/openapi.yaml").read_text())
        def resolve(pointer):
            value = contract
            for part in pointer.removeprefix("#/").split("/"):
                value = value[part.replace("~1", "/").replace("~0", "~")]
            return value
        def check_refs(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    self.assertIsNotNone(resolve(value["$ref"]))
                for child in value.values():
                    check_refs(child)
            elif isinstance(value, list):
                for child in value:
                    check_refs(child)
        check_refs(contract)
        for path, item in root["paths"].items():
            if "$ref" in item:
                filename, fragment = item["$ref"].split("#", 1)
                self.assertEqual(filename, "./publishing-insights.openapi.json")
                self.assertEqual(resolve("#" + fragment), contract["paths"][path])
        request = self.request()
        receipt = self.service.submit(request)
        self.clock.now += timedelta(hours=1)
        self.insights.maintain()
        for name, payload in [("Receipt", receipt.model_dump(mode="json")),
                              ("MetricsResponse", self.insights.metrics("account-1").model_dump(mode="json"))]:
            schema = {"$ref": f"#/components/schemas/{name}", "components": contract["components"]}
            Draft202012Validator(schema, format_checker=FORMATS).validate(payload)

    def test_request_schema_matches_http_rejections(self):
        schema = json.loads((ROOT / "contracts/events/v1/task.publish_requested.v1.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FORMATS)
        valid = self.request().model_dump(mode="json")
        validator.validate(valid)
        for bad in ({**valid, "idempotency_key": " "},
                    {**valid, "occurred_at": "2026-09-22T12:00:00"},
                    {**valid, "data": {**valid["data"], "task_id": 123}},
                    {**valid, "data": {**valid["data"], "approved": True}}):
            self.assertTrue(list(validator.iter_errors(bad)))

    def test_real_http_business_context_client_validates_identity_and_shape(self):
        request = self.request()
        valid = self.source.get(request.data.task_id).model_dump(mode="json")
        state = {"status": 200, "payload": valid, "requests": []}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state["requests"].append((self.path, self.headers.get("Authorization")))
                self.send_response(state["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Location", "/should-not-follow")
                self.end_headers()
                self.wfile.write(json.dumps(state["payload"]).encode())

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            source = HttpTaskSource(f"http://127.0.0.1:{server.server_port}", "business-only")
            self.assertEqual(source.get(request.data.task_id), self.source.get(request.data.task_id))
            self.assertEqual(state["requests"][-1],
                             ("/api/v1/internal/tasks/task-key-1/publishing-context", "Bearer business-only"))
            for code, payload in [(503, valid), (302, valid), (404, {}),
                                  (200, {**valid, "task_id": "different"}),
                                  (200, {**valid, "video_approved": "true"}),
                                  (200, {**valid, "qc_score": 101})]:
                state.update(status=code, payload=payload)
                before = len(state["requests"])
                with self.assertRaises(DependencyUnavailable):
                    source.get(request.data.task_id)
                self.assertEqual(len(state["requests"]), before + 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class AdapterTests(unittest.TestCase):
    def test_real_simulator_three_paths_and_failure_distribution(self):
        simulator = SimulatedPublisher()
        combinations = set()
        failures = 0
        for i in range(10000):
            first = simulator.publish(str(i), 1, "fixed")
            second = simulator.publish(str(i), 2, "fixed")
            failures += not first.success
            combinations.add((first.success, second.success))
            self.assertEqual(first, simulator.publish(str(i), 1, "fixed"))
        self.assertTrue({(True, True), (False, True), (False, False)} <= combinations)
        self.assertTrue(800 <= failures <= 1200, failures)
        script = "import sys; sys.path.insert(0, sys.argv[1]); from publishing.adapters import SimulatedPublisher; print(SimulatedPublisher().publish('key', 1, 'seed').model_dump_json())"
        output = subprocess.check_output([sys.executable, "-I", "-c", script, str(MODULE)], text=True).strip()
        self.assertEqual(output, simulator.publish("key", 1, "seed").model_dump_json())

    def test_metric_quality_changes_distribution_not_guaranteed_rank(self):
        context = TaskContext(task_id="t", account_id="a", status="SCHEDULED", scheduled_at=START,
                              media_version="v", approved_media_version="v", script_approved=True,
                              video_approved=True, qc_passed=True, risk_level="LOW")
        low, high = [], []
        for i in range(1000):
            low.append(simulate_metrics(context.model_copy(update={"qc_score": 0.0, "review_score": 0.0}),
                                        str(i), "s", 3600).plays)
            high.append(simulate_metrics(context.model_copy(update={"qc_score": 100.0, "review_score": 100.0}),
                                         str(i), "s", 3600).plays)
        self.assertGreater(sum(high), sum(low))
        self.assertLess(min(high), max(low))

    def test_settings_reject_unscoped_or_shared_credentials(self):
        for tokens in ({WORKFLOW: frozenset({"a"})}, {METRICS: frozenset()}, {"short": frozenset({"a"})}):
            with self.assertRaises(ValueError):
                Settings("unused", WORKFLOW, tokens)


if __name__ == "__main__":
    unittest.main(verbosity=2)
