from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from uuid import uuid4

from services.business_api.app import BusinessApi
from services.business_api.db import Database
from services.business_api.state_machine import InvalidTransition, validate_transition
from services.workflow.engine import BEIJING, Workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.database = Database()
        self.now = datetime(2026, 9, 22, 9, tzinfo=BEIJING)
        self.workflow = Workflow(self.database, lambda: self.now)
        self.first = self.database.create_account("first", "password-first")
        self.second = self.database.create_account("second", "password-second")

    def tearDown(self):
        self.database.close()

    def task(self, risk="LOW", account="publisher"):
        task = self.database.create_task(self.first["id"], "Demo", risk_level=risk)
        self.workflow.register(task["id"], account)
        self.database.add_script_version(self.first["id"], task["id"], "A short factual script with evidence.")
        return task["id"]

    def event(self, task_id, event_type, **data):
        key = str(uuid4())
        if event_type == "task.published.v1" or data.get("failed_step") == "publishing":
            key = self.workflow.inspect(task_id)["active_request_key"]
        return {"event_id": str(uuid4()), "event_type": event_type, "occurred_at": self.now.isoformat(),
                "correlation_id": task_id, "idempotency_key": key, "data": {"task_id": task_id, **data}}

    def script(self, task_id, risk="LOW"):
        return self.workflow.receive(self.event(task_id, "task.script_reviewed.v1", script_version=1, risk_level=risk, rule_hits=[], requires_human_review=True))

    def approve(self, task_id, kind, actor=None, decision="APPROVED"):
        self.database.add_approval((actor or self.first)["id"], task_id, kind, decision)
        self.workflow.review(task_id)

    def media(self, task_id, passed=True):
        self.database.add_media_version(self.first["id"], task_id, "mock://video.mp4")
        self.workflow.receive(self.event(task_id, "task.media_qc_completed.v1", media_version=1, asset_ids=["video"], qc_decision="PASS" if passed else "FAIL", failures=[]))

    def scheduled(self, risk="LOW", account="publisher"):
        task_id = self.task(risk, account)
        self.script(task_id, risk)
        self.approve(task_id, "SCRIPT")
        if risk == "HIGH":
            self.approve(task_id, "SCRIPT", self.second)
        self.media(task_id)
        self.approve(task_id, "VIDEO")
        if risk == "HIGH":
            self.approve(task_id, "VIDEO", self.second)
        return task_id

    def failure(self, task_id, retryable=True):
        task = self.workflow.inspect(task_id)
        stage = {"SCRIPT_GENERATING": "script", "MEDIA_GENERATING": "media", "PUBLISHING": "publish"}[task["status"]]
        if stage == "publish":
            return self.event(task_id, "task.failed.v1", failed_step="publishing", error_code="SUPPLIER",
                              retryable=False, attempt=2, request_key=task["active_request_key"], receipt_id=task["receipt_id"])
        return self.event(task_id, "task.failed.v1", failed_step="render" if stage == "media" else stage,
                          error_code="SUPPLIER", retryable=retryable, attempt=task["attempts"].get(stage, 0), request_key=task["active_request_key"])

    def test_low_full_lifecycle_and_duplicate_publication_tick(self):
        task_id = self.scheduled()
        self.assertEqual(self.workflow.inspect(task_id)["status"], "SCHEDULED")
        self.now = self.now.replace(hour=12)
        self.workflow.tick()
        self.workflow.tick()
        requests = [event for event in self.workflow.pending() if event["event_type"] == "task.publish_requested.v1"]
        self.assertEqual(len(requests), 1)
        self.workflow.receive(self.event(task_id, "task.published.v1", receipt_id=requests[0]["data"]["receipt_id"], platform_post_id="mock-post", published_at=self.now.isoformat()))
        self.assertEqual(self.database.get_task(task_id)["status"], "PUBLISHED")

    def test_high_requires_distinct_people_at_each_gate(self):
        task_id = self.task("HIGH")
        self.script(task_id, "HIGH")
        self.approve(task_id, "SCRIPT")
        self.approve(task_id, "SCRIPT")
        self.assertEqual(self.workflow.inspect(task_id)["status"], "SCRIPT_PENDING_APPROVAL")
        self.approve(task_id, "SCRIPT", self.second)
        self.media(task_id)
        self.approve(task_id, "VIDEO")
        self.assertEqual(self.workflow.inspect(task_id)["status"], "VIDEO_PENDING_APPROVAL")
        self.approve(task_id, "VIDEO", self.second)
        self.assertEqual(self.workflow.inspect(task_id)["status"], "SCHEDULED")

    def test_rejection_wins_and_blocked_cannot_produce(self):
        task_id = self.task("HIGH")
        self.script(task_id, "HIGH")
        self.approve(task_id, "SCRIPT")
        self.approve(task_id, "SCRIPT", self.second, "REJECTED")
        self.assertEqual(self.workflow.inspect(task_id)["status"], "SCRIPT_REJECTED")
        blocked = self.task(account="other")
        self.script(blocked, "BLOCKED")
        self.assertEqual(self.workflow.inspect(blocked)["status"], "SCRIPT_REJECTED")
        self.assertFalse(any(event["event_type"] == "task.media_requested.v1" for event in self.workflow.pending()))

    def test_exact_cutoff_pauses_and_cancel_is_terminal(self):
        task_id = self.task()
        self.script(task_id)
        self.now = self.now.replace(hour=11, minute=30)
        self.approve(task_id, "SCRIPT")
        self.assertEqual(self.workflow.inspect(task_id)["status"], "PAUSED")
        self.workflow.cancel(task_id)
        self.workflow.receive(self.event(task_id, "task.script_reviewed.v1", script_version=1, risk_level="LOW"))
        self.assertEqual(self.workflow.inspect(task_id)["status"], "CANCELLED")
        with self.assertRaises(ValueError):
            self.workflow.resume(task_id)

    def test_pause_reschedule_preserves_approvals(self):
        task_id = self.scheduled()
        before = self.workflow.inspect(task_id)["approvals"]
        self.workflow.pause(task_id)
        self.assertTrue(self.workflow.reschedule(task_id, "2026-09-23"))
        self.assertEqual(self.workflow.inspect(task_id)["approvals"], before)
        self.now = self.now.replace(hour=12)
        self.workflow.tick()
        self.assertEqual(self.workflow.inspect(task_id)["status"], "SCHEDULED")

    def test_new_script_invalidates_approvals_and_old_events(self):
        task_id = self.scheduled()
        self.workflow.pause(task_id)
        self.database.add_script_version(self.first["id"], task_id, "Updated script")
        with self.assertRaises(ValueError):
            self.workflow.reschedule(task_id, "2026-09-23")
        self.workflow.restart(task_id, script_version=2)
        self.assertEqual(self.workflow.inspect(task_id)["approvals"], {"SCRIPT": {}, "VIDEO": {}})
        self.script(task_id)
        self.assertEqual(self.workflow.inspect(task_id)["status"], "SCRIPT_GENERATING")

    def test_qc_fail_never_reaches_human_video_approval(self):
        task_id = self.task()
        self.script(task_id)
        self.approve(task_id, "SCRIPT")
        self.media(task_id, False)
        self.assertEqual(self.workflow.inspect(task_id)["status"], "VIDEO_REJECTED")

    def test_retry_delays_and_exhaustion(self):
        task_id = self.task()
        for delay in (1, 5, 15):
            self.workflow.receive(self.failure(task_id))
            self.assertEqual(self.workflow.inspect(task_id)["status"], "FAILED")
            self.now += timedelta(minutes=delay, seconds=-1)
            self.workflow.tick()
            self.assertEqual(self.workflow.inspect(task_id)["status"], "FAILED")
            self.now += timedelta(seconds=1)
            self.workflow.tick()
            self.assertEqual(self.workflow.inspect(task_id)["status"], "SCRIPT_GENERATING")
        self.workflow.receive(self.failure(task_id))
        self.assertEqual(self.workflow.inspect(task_id)["status"], "PAUSED")

    def test_media_retry_preserves_versions_and_script(self):
        task_id = self.task()
        self.script(task_id)
        self.approve(task_id, "SCRIPT")
        before = [event for event in self.workflow.pending() if event["event_type"] == "task.media_requested.v1"][0]
        self.workflow.receive(self.failure(task_id))
        self.now += timedelta(minutes=1)
        self.workflow.tick()
        after = [event for event in self.workflow.pending() if event["event_type"] == "task.media_requested.v1"][0]
        self.assertEqual(after["data"]["resume_from"], "render")
        for field in ("script", "script_version", "media_version", "voice_version"):
            self.assertEqual(before["data"][field], after["data"][field])

    def test_final_publish_failure_pauses_without_workflow_retry(self):
        task_id = self.scheduled()
        self.now = self.now.replace(hour=12)
        self.workflow.tick()
        receipt = self.workflow.inspect(task_id)["receipt_id"]
        self.workflow.receive(self.failure(task_id))
        self.now += timedelta(minutes=1)
        self.workflow.tick()
        self.assertEqual(self.workflow.inspect(task_id)["receipt_id"], receipt)
        self.assertEqual(self.workflow.inspect(task_id)["status"], "PAUSED")
        self.assertEqual(self.workflow.inspect(task_id)["publication_attempts"], 2)
        self.assertFalse(any(event["event_type"] == "task.publish_requested.v1" for event in self.workflow.pending()))
        with self.assertRaises(ValueError):
            self.workflow.reschedule(task_id, "2026-09-23")

    def test_scan_times_retries_and_stale_results(self):
        topic = self.database.create_topic(self.first["id"], "AI", ["models"])
        self.now = self.now.replace(hour=8, minute=59)
        self.workflow.tick()
        self.assertEqual(self.workflow.pending(), [])
        self.now = self.now.replace(hour=9, minute=0)
        self.workflow.tick()
        self.workflow.tick()
        self.assertEqual(len(self.workflow.pending()), 1)
        first = self.workflow.pending()[0]
        self.workflow.scan_result(topic["id"], "2026-09-22", first["idempotency_key"], False)
        self.now = self.now.replace(minute=9)
        self.workflow.tick()
        self.assertEqual(len(self.workflow.pending()), 0)
        self.now = self.now.replace(minute=10)
        self.workflow.tick()
        second = self.workflow.pending()[-1]
        self.assertFalse(self.workflow.scan_result(topic["id"], "2026-09-22", first["idempotency_key"], True))
        self.workflow.scan_result(topic["id"], "2026-09-22", second["idempotency_key"], False)
        self.workflow.retry_search(topic["id"])
        third = self.workflow.pending()[-1]
        self.workflow.scan_result(topic["id"], "2026-09-22", third["idempotency_key"], False)
        with self.assertRaises(ValueError):
            self.workflow.retry_search(topic["id"])

    def test_duplicate_events_and_atomic_rollback(self):
        task_id = self.task()
        event = self.event(task_id, "task.script_reviewed.v1", script_version=1, risk_level="WRONG")
        with self.assertRaises(ValueError):
            self.workflow.receive(event)
        self.assertEqual(self.workflow.inspect(task_id)["status"], "SCRIPT_GENERATING")
        event["data"]["risk_level"] = "LOW"
        self.assertTrue(self.workflow.receive(event))
        self.assertFalse(self.workflow.receive(event))
        event["event_id"] = str(uuid4())
        self.assertFalse(self.workflow.receive(event))

    def test_only_one_slot_and_changed_content_blocks_publish(self):
        first = self.scheduled()
        second = self.scheduled()
        self.assertEqual(self.workflow.inspect(second)["status"], "PAUSED")
        self.database.add_script_version(self.first["id"], first, "changed")
        self.now = self.now.replace(hour=12)
        self.workflow.tick()
        self.assertEqual(self.workflow.inspect(first)["status"], "PAUSED")

    def test_cancel_suppresses_queued_media(self):
        task_id = self.task()
        self.script(task_id)
        self.approve(task_id, "SCRIPT")
        self.workflow.cancel(task_id)
        self.assertFalse(any(event["event_type"] == "task.media_requested.v1" for event in self.workflow.pending()))

    def test_restart_preserves_inbox_and_outbox(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "workflow.sqlite"
            database = Database(path)
            workflow = Workflow(database, lambda: self.now)
            task = database.create_task("actor", "Persistent")
            workflow.register(task["id"], "publisher")
            pending = workflow.pending()
            database.close()
            reopened = Database(path)
            try:
                restored = Workflow(reopened, lambda: self.now)
                self.assertEqual(restored.pending(), pending)
                restored.acknowledge(pending[0]["event_id"])
                self.assertEqual(restored.pending(), [])
            finally:
                reopened.close()

    def test_business_api_approval_is_consumed_on_tick(self):
        task_id = self.task()
        self.script(task_id)
        api = BusinessApi(self.database)
        token = api.request("POST", "/api/v1/auth/login", body={"username": "first", "password": "password-first"}).body["access_token"]
        response = api.request("POST", f"/api/v1/tasks/{task_id}/approvals", {"Authorization": "Bearer " + token}, {"kind": "SCRIPT", "decision": "APPROVED"})
        self.assertEqual(response.status, 201)
        self.workflow.tick()
        self.assertEqual(self.workflow.inspect(task_id)["status"], "MEDIA_GENERATING")

    def test_illegal_shortcuts_and_terminal_edges(self):
        for current, target in (("SCRIPT_AUTO_REVIEWING", "SCRIPT_APPROVED"), ("VIDEO_APPROVED", "PUBLISHING"), ("CANCELLED", "DISCOVERED"), ("PUBLISHED", "PAUSED")):
            with self.assertRaises(InvalidTransition):
                validate_transition(current, target)

    def test_managed_state_endpoint_cannot_bypass_workflow(self):
        task_id = self.task()
        response = BusinessApi(self.database).request("POST", f"/api/v1/internal/tasks/{task_id}/transitions", {"X-Internal-Service": "workflow"}, {"target_status": "SCRIPT_AUTO_REVIEWING"})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.database.get_task(task_id)["status"], "SCRIPT_GENERATING")

    def test_stale_and_premature_approval_rejected(self):
        task_id = self.task()
        with self.assertRaises(ValueError):
            self.database.add_approval(self.first["id"], task_id, "SCRIPT", "APPROVED")
        self.script(task_id)
        with self.assertRaises(ValueError):
            self.database.add_approval(self.first["id"], task_id, "SCRIPT", "APPROVED", expected_version=2)

    def test_paused_media_resume_keeps_upstream_versions(self):
        task_id = self.task()
        self.script(task_id)
        self.approve(task_id, "SCRIPT")
        self.workflow.receive(self.failure(task_id, retryable=False))
        self.workflow.resume(task_id)
        request = next(event for event in self.workflow.pending() if event["event_type"] == "task.media_requested.v1")
        self.assertEqual(request["data"]["resume_from"], "render")
        self.assertEqual(request["data"]["media_version"], 1)

    def test_media_rebuild_keeps_script_approval(self):
        task_id = self.scheduled()
        votes = self.workflow.inspect(task_id)["approvals"]["SCRIPT"]
        self.workflow.pause(task_id)
        self.workflow.restart(task_id, media_version=2)
        self.assertEqual(self.workflow.inspect(task_id)["approvals"]["SCRIPT"], votes)
        self.assertEqual(self.workflow.inspect(task_id)["approvals"]["VIDEO"], {})
        self.database.add_media_version("media", task_id, "mock://v2")
        self.workflow.receive(self.event(task_id, "task.media_qc_completed.v1", media_version=2, asset_ids=["new-video"], qc_decision="PASS", failures=[]))
        self.assertEqual(self.workflow.inspect(task_id)["status"], "VIDEO_PENDING_APPROVAL")

    def test_missed_publish_day_pauses(self):
        task_id = self.scheduled()
        self.now += timedelta(days=1)
        self.workflow.tick()
        self.assertEqual(self.workflow.inspect(task_id)["status"], "PAUSED")

    def test_event_required_payloads_match_shared_contracts(self):
        task_id = self.scheduled()
        self.now = self.now.replace(hour=12)
        self.workflow.tick()
        root = Path(__file__).resolve().parents[2]
        rows = self.database.connection.execute("SELECT payload FROM workflow_outbox")
        for row in rows:
            event = json.loads(row[0])
            path = root / "contracts" / "events" / "v1" / (event["event_type"] + ".json")
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(set(schema["required"]) <= event.keys())
            self.assertTrue(set(schema["properties"]["data"]["required"]) <= event["data"].keys())

    def test_durable_event_deduplication_after_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "state.sqlite")
            workflow = Workflow(database, lambda: self.now)
            task = database.create_task("actor", "Durable")
            workflow.register(task["id"], "publisher")
            database.add_script_version("content", task["id"], "Script")
            event = self.event(task["id"], "task.script_reviewed.v1", script_version=1, risk_level="LOW")
            workflow.receive(event)
            database.close()
            database = Database(Path(folder) / "state.sqlite")
            try:
                workflow = Workflow(database, lambda: self.now)
                self.assertFalse(workflow.receive(event))
            finally:
                database.close()

    @unittest.skipUnless(os.environ.get("MEDIA_PRODUCTION_SRC"), "set MEDIA_PRODUCTION_SRC to member 5 src directory")
    def test_member_five_pipeline_consumes_real_workflow_event(self):
        sys.path.insert(0, os.environ["MEDIA_PRODUCTION_SRC"])
        from media_production.pipeline import MediaProductionPipeline
        from services.workflow.media_adapter import LocalMediaAdapter

        task_id = self.task()
        self.script(task_id)
        self.approve(task_id, "SCRIPT")
        request = next(event for event in self.workflow.pending() if event["event_type"] == "task.media_requested.v1")
        pipeline = MediaProductionPipeline()
        adapter = LocalMediaAdapter(self.workflow, pipeline, lambda result, assets: "mock://member-five")
        self.assertTrue(adapter.dispatch(request))
        self.assertEqual(self.workflow.inspect(task_id)["status"], "VIDEO_PENDING_APPROVAL")
        self.assertFalse(self.workflow.receive(pipeline.handle_media_requested(request)))
        self.assertFalse(adapter.dispatch(request))

    @unittest.skipUnless(os.environ.get("MEDIA_PRODUCTION_SRC"), "set MEDIA_PRODUCTION_SRC to member 5 src directory")
    def test_member_five_retry_reuses_voice_after_render_failure(self):
        sys.path.insert(0, os.environ["MEDIA_PRODUCTION_SRC"])
        from media_production.mock_provider import DeterministicMockProvider
        from media_production.pipeline import MediaProductionPipeline

        class FailingProvider(DeterministicMockProvider):
            calls = 0
            voices = 0

            def synthesize_voice(self, *args):
                self.voices += 1
                return super().synthesize_voice(*args)

            def render_video(self, *args):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("simulated render outage")
                return super().render_video(*args)

        task_id = self.task()
        self.script(task_id)
        self.approve(task_id, "SCRIPT")
        provider = FailingProvider()
        pipeline = MediaProductionPipeline(provider)
        request = next(event for event in self.workflow.pending() if event["event_type"] == "task.media_requested.v1")
        with self.assertRaises(RuntimeError):
            pipeline.handle_media_requested(request)
        self.workflow.receive(self.failure(task_id))
        self.now += timedelta(minutes=1)
        self.workflow.tick()
        request = next(event for event in self.workflow.pending() if event["event_type"] == "task.media_requested.v1")
        result = pipeline.handle_media_requested(request)
        self.assertEqual(result["data"]["qc_decision"], "PASS")
        self.assertEqual(provider.voices, 1)


if __name__ == "__main__":
    unittest.main()
