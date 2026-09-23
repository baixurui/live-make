import sys
import unittest
from pathlib import Path


SERVICE_SRC = Path(__file__).parents[2] / "services" / "media-production" / "src"
sys.path.insert(0, str(SERVICE_SRC))

from media_production.pipeline import MediaProductionPipeline
from media_production.asset_adapter import resolve_video_uri, serialize_asset


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = MediaProductionPipeline()
        self.event = {
            "event_id": "event-1",
            "event_type": "task.media_requested.v1",
            "occurred_at": "2026-09-22T00:00:00Z",
            "correlation_id": "corr-1",
            "idempotency_key": "task-1-media-v1",
            "data": {
                "task_id": "task-1",
                "script_version": 1,
                "media_version": 1,
                "script": "今天介绍一个值得关注的主题。",
                "output_spec": {},
            },
        }

    def test_generates_qc_event_with_fixed_assets(self):
        result = self.pipeline.handle_media_requested(self.event)
        self.assertEqual(result["event_type"], "task.media_qc_completed.v1")
        self.assertEqual(result["data"]["qc_decision"], "PASS")
        self.assertEqual(len(result["data"]["asset_ids"]), 3)
        self.assertEqual(result["data"]["failures"], [])
        self.assertEqual(result["data"]["human_review_flags"], [])
        self.assertFalse(result["data"]["requires_human_review"])
        self.assertEqual(len(self.pipeline.asset_history), 3)
        video = self.pipeline.asset_history[-1]
        self.assertEqual(video.upstream_asset_ids, tuple(result["data"]["asset_ids"][:2]))

    def test_retry_reuses_upstream_voice_asset(self):
        first = self.pipeline.handle_media_requested(self.event)
        self.event["idempotency_key"] = "task-1-media-v2"
        self.event["data"]["media_version"] = 2
        second = self.pipeline.handle_media_requested(self.event)
        self.assertEqual(first["data"]["asset_ids"][1], second["data"]["asset_ids"][1])

    def test_video_uri_and_asset_metadata_are_adapter_ready(self):
        result = self.pipeline.handle_media_requested(self.event)
        uri = resolve_video_uri(result, self.pipeline.asset_history)
        self.assertTrue(uri.startswith("mock://video/"))
        video = self.pipeline.asset_history[-1]
        metadata = serialize_asset(video)
        self.assertEqual(metadata["uri"], uri)
        self.assertEqual(metadata["metadata"]["upstream_asset_ids"], list(video.upstream_asset_ids))

    def test_versions_must_match_workflow_integer_contract(self):
        self.event["data"]["media_version"] = "media-v1"
        with self.assertRaises(ValueError):
            self.pipeline.handle_media_requested(self.event)

    def test_reprocessing_same_key_is_idempotent(self):
        first = self.pipeline.handle_media_requested(self.event)
        second = self.pipeline.handle_media_requested(self.event)
        self.assertEqual(first, second)

    def test_request_requires_media_event_type(self):
        self.event["event_type"] = "task.script_approved.v1"
        with self.assertRaises(ValueError):
            self.pipeline.handle_media_requested(self.event)


if __name__ == "__main__":
    unittest.main()
