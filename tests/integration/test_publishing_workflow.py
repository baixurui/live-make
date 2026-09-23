"""Real HTTP transport across Business API, publishing, and workflow databases."""
from copy import deepcopy
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/publishing-insights"))

import httpx
import uvicorn
from publishing.adapters import HttpTaskSource, SimulatedPublisher
from publishing.api import Settings, create_app
from publishing.models import PublishRequest, TaskContext
from services.business_api.app import BusinessApi
from services.business_api.db import Database
from services.business_api.server import RequestHandler
from services.workflow.engine import BEIJING, Workflow
from services.workflow.publishing_adapter import PublishingAdapter


WORKFLOW_TOKEN = "integration-workflow-token"
BUSINESS_TOKEN = "integration-business-token"


class ObservedSimulator(SimulatedPublisher):
    def __init__(self):
        self.calls = []
        self.after_attempt = None

    def publish(self, key, attempt, seed):
        result = super().publish(key, attempt, seed)
        self.calls.append((key, attempt, result.success))
        if self.after_attempt:
            self.after_attempt(attempt)
        return result


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "business.sqlite3"
        self.database = Database(self.path)
        self.addCleanup(self.database.close)
        self.now = datetime(2026, 9, 22, 9, tzinfo=BEIJING)
        self.workflow = Workflow(self.database, lambda: self.now)
        self.actor = self.database.create_account("reviewer", "integration-only-password")
        self.api_db = Database(self.path)
        self.addCleanup(self.api_db.close)
        self.api = BusinessApi(self.api_db, publishing_token=BUSINESS_TOKEN)
        handler = type("IntegrationHandler", (RequestHandler,), {"api": self.api})
        self.business_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.business_thread = threading.Thread(target=self.business_server.serve_forever, daemon=True)
        self.business_thread.start()
        self.addCleanup(self.stop_business)
        self.business_url = f"http://127.0.0.1:{self.business_server.server_port}"
        self.source = HttpTaskSource(self.business_url, BUSINESS_TOKEN)

    def stop_business(self):
        self.business_server.shutdown()
        self.business_server.server_close()
        self.business_thread.join(5)

    def event(self, task_id, kind, **data):
        return {"event_id": str(uuid4()), "event_type": kind, "occurred_at": self.now.isoformat(),
                "correlation_id": task_id, "idempotency_key": str(uuid4()),
                "data": {"task_id": task_id, **data}}

    def schedule(self):
        task = self.database.create_task(self.actor["id"], "Real integration task")
        task_id = task["id"]
        self.workflow.register(task_id, "publishing-account")
        self.database.add_script_version(self.actor["id"], task_id, "Reviewed script")
        self.workflow.receive(self.event(task_id, "task.script_reviewed.v1", script_version=1,
                                         risk_level="LOW", rule_hits=[], requires_human_review=True))
        self.database.add_approval(self.actor["id"], task_id, "SCRIPT", "APPROVED")
        self.workflow.review(task_id)
        self.database.add_media_version(self.actor["id"], task_id, "test://media.mp4")
        self.workflow.receive(self.event(task_id, "task.media_qc_completed.v1", media_version=1,
                                         asset_ids=["video"], qc_decision="PASS", failures=[]))
        self.database.add_approval(self.actor["id"], task_id, "VIDEO", "APPROVED")
        self.workflow.review(task_id)
        self.assertEqual(self.database.get_task(task_id)["status"], "SCHEDULED")
        self.now = self.now.replace(hour=12)
        self.workflow.tick()
        self.request = next(event for event in self.workflow.pending() if event["event_type"] == "task.publish_requested.v1")
        PublishRequest.model_validate_json(json.dumps(self.request))
        return task_id

    def start_publishing(self, outcomes):
        simulator = SimulatedPublisher()
        key = self.request["idempotency_key"]
        seed = next(str(i) for i in range(100000) if all(
            simulator.publish(key, attempt, str(i)).success == expected
            for attempt, expected in enumerate(outcomes, 1)))
        self.publisher = ObservedSimulator()
        settings = Settings(str(Path(self.temp.name) / "publishing.sqlite3"), WORKFLOW_TOKEN,
                            simulation_seed=seed, business_api_url=self.business_url,
                            business_api_token=BUSINESS_TOKEN)
        self.app = create_app(settings, publisher=self.publisher, clock=lambda: self.now, worker_enabled=False)
        ready = threading.Event()
        class Server(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets)
                ready.set()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        server = Server(uvicorn.Config(self.app, log_level="error", access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        def stop():
            server.should_exit = True
            thread.join(10)
            self.assertFalse(thread.is_alive(), "publishing HTTP server failed to stop")
        self.addCleanup(stop)
        self.assertTrue(ready.wait(10), "publishing HTTP server failed to start")
        self.publishing_url = f"http://127.0.0.1:{port}"
        self.adapter = PublishingAdapter(self.workflow, self.publishing_url, WORKFLOW_TOKEN)

    def receipt(self):
        return self.app.state.service.receipt(self.request["data"]["receipt_id"])

    def test_first_success_real_http_and_duplicate_request(self):
        task_id = self.schedule()
        context = self.source.get(task_id)
        self.assertEqual(context.media_version, 1)
        self.assertTrue(context.script_approved and context.video_approved)
        self.start_publishing((True,))
        self.adapter.sync()
        self.assertEqual(self.receipt().status, "SUCCEEDED")
        self.assertEqual(self.workflow.inspect(task_id)["status"], "PUBLISHING")
        response = self.adapter._request("POST", "/api/v1/internal/publishing/requests", self.request)
        self.assertEqual(response["receipt_id"], self.receipt().receipt_id)
        self.adapter.sync()
        self.assertEqual(self.database.get_task(task_id)["status"], "PUBLISHED")
        self.assertEqual(len(self.publisher.calls), 1)
        self.assertEqual(self.app.state.service.events(), [])

    def test_first_failure_second_success_is_only_retried_by_publisher(self):
        task_id = self.schedule()
        self.start_publishing((False, True))
        self.adapter.sync()
        self.adapter.sync()
        self.assertEqual(self.database.get_task(task_id)["status"], "PUBLISHED")
        self.assertEqual([call[2] for call in self.publisher.calls], [False, True])
        self.assertEqual(self.workflow.inspect(task_id)["attempts"].get("publish", 0), 0)

    def test_two_failures_pause_and_ticks_never_republish(self):
        task_id = self.schedule()
        self.start_publishing((False, False))
        self.adapter.sync()
        event = self.app.state.service.events()[0]
        self.assertEqual(event["data"]["request_key"], self.request["idempotency_key"])
        self.assertEqual(event["data"]["attempt"], 2)
        self.assertFalse(event["data"]["retryable"])
        self.adapter.sync()
        self.assertEqual(self.database.get_task(task_id)["status"], "PAUSED")
        for minutes in (1, 5, 15):
            self.now += timedelta(minutes=minutes)
            self.workflow.tick()
            self.adapter.sync()
        self.assertEqual(len(self.publisher.calls), 2)
        self.assertIsNone(self.workflow.inspect(task_id)["retry_at"])

    def test_ack_loss_and_workflow_restart_do_not_repeat_state_transition(self):
        task_id = self.schedule()
        self.start_publishing((False, False))
        self.adapter.sync()
        original = self.adapter._request
        def drop_ack(method, path, body=None):
            if path.endswith("/ack"):
                raise URLError("injected ack connection loss")
            return original(method, path, body)
        with patch.object(self.adapter, "_request", side_effect=drop_ack):
            with self.assertRaises(URLError):
                self.adapter.sync()
        self.assertEqual(self.database.get_task(task_id)["status"], "PAUSED")
        self.assertEqual(len(self.app.state.service.events()), 1)
        restarted_db = Database(self.path)
        self.addCleanup(restarted_db.close)
        restarted = Workflow(restarted_db, lambda: self.now)
        PublishingAdapter(restarted, self.publishing_url, WORKFLOW_TOKEN).sync()
        self.assertEqual(self.app.state.service.events(), [])
        transitions = self.database.connection.execute(
            "SELECT details FROM audit_logs WHERE resource_id=? AND action='task.transitioned'", (task_id,)).fetchall()
        self.assertEqual(sum(json.loads(row[0])["to"] == "PAUSED" for row in transitions), 1)
        self.assertEqual(len(self.publisher.calls), 2)

    def test_request_response_loss_replays_same_publication(self):
        task_id = self.schedule()
        self.start_publishing((True,))
        original = self.adapter._request
        def lose_response(method, path, body=None):
            result = original(method, path, body)
            if path.endswith("/requests"):
                raise URLError("accepted but response lost")
            return result
        with patch.object(self.adapter, "_request", side_effect=lose_response):
            with self.assertRaises(URLError):
                self.adapter.sync()
        self.adapter.sync()
        self.assertEqual(self.database.get_task(task_id)["status"], "PUBLISHED")
        self.assertEqual(len(self.publisher.calls), 1)

    def test_invalid_final_failure_cannot_change_workflow_state(self):
        task_id = self.schedule()
        self.start_publishing((False, False))
        self.adapter.sync()
        valid = self.app.state.service.events()[0]
        for changes in ({"request_key": "stale"}, {"receipt_id": "stale"}, {"attempt": 3},
                        {"attempt": True}, {"retryable": True}, {"failed_step": "publish"}):
            invalid = deepcopy(valid)
            invalid["data"].update(changes)
            with self.assertRaises(ValueError):
                self.workflow.receive(invalid)
            self.assertEqual(self.database.get_task(task_id)["status"], "PUBLISHING")
        stale = deepcopy(valid)
        stale["idempotency_key"] = "old-key"
        with self.assertRaises(ValueError):
            self.workflow.receive(stale)
        self.adapter.sync()
        self.assertEqual(self.database.get_task(task_id)["status"], "PAUSED")

    def test_context_auth_and_live_approval_evidence(self):
        task_id = self.schedule()
        path = f"/api/v1/internal/tasks/{task_id}/publishing-context"
        session = self.api.request("POST", "/api/v1/auth/login",
                                   body={"username": "reviewer", "password": "integration-only-password"}).body["access_token"]
        for headers in ({}, {"X-Internal-Service": "publishing-insights"},
                        {"Authorization": f"Bearer {session}"}, {"Authorization": "Bearer wrong"}):
            self.assertEqual(httpx.get(self.business_url + path, headers=headers).status_code, 401)
        self.assertTrue(self.source.get(task_id).video_approved)
        self.database.set_account(self.actor["id"], self.actor["id"], enabled=False)
        self.assertFalse(self.source.get(task_id).video_approved)
        self.database.set_account(self.actor["id"], self.actor["id"], enabled=True)
        self.database.add_media_version(self.actor["id"], task_id, "test://new.mp4")
        changed = self.source.get(task_id)
        self.assertEqual(changed.media_version, 2)
        self.assertFalse(changed.video_approved)
        self.assertFalse(changed.qc_passed)
        with self.assertRaises(ValueError):
            TaskContext.model_validate_json(changed.model_dump_json().replace('"media_version":2', '"media_version":"2"'))

    def test_approval_revoked_between_actual_attempts_pauses(self):
        task_id = self.schedule()
        self.start_publishing((False, True))
        def revoke(attempt):
            with self.api._lock:
                self.api_db.set_account(self.actor["id"], self.actor["id"], enabled=False)
        self.publisher.after_attempt = revoke
        self.adapter.sync()
        self.assertEqual(self.receipt().status, "ABORTED")
        self.assertEqual(len(self.publisher.calls), 1)
        self.adapter.sync()
        self.assertEqual(self.database.get_task(task_id)["status"], "PAUSED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
