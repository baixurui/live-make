import sys
import unittest
from pathlib import Path


SERVICE_SRC = Path(__file__).parents[2] / "services" / "media-production" / "src"
sys.path.insert(0, str(SERVICE_SRC))

from media_production.models import MediaProbe, OutputSpec
from media_production.qc import run_quality_checks
from media_production.subtitles import build_simplified_chinese_subtitles


def valid_probe(**overrides):
    values = {
        "width": 720,
        "height": 1280,
        "container": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac",
        "fps": 25,
        "video_bitrate_bps": 2_500_000,
        "duration_seconds": 10,
        "audio_peak": 0.2,
        "decodable": True,
        "subtitle_track": build_simplified_chinese_subtitles("这是测试内容。", duration_seconds=10),
    }
    values.update(overrides)
    return MediaProbe(**values)


class QCTests(unittest.TestCase):
    def test_valid_media_passes(self):
        result = run_quality_checks(valid_probe(), OutputSpec())
        self.assertEqual(result.decision.value, "PASS")
        self.assertEqual(result.failures, ())

    def test_hard_stop_rules_are_reported(self):
        cases = {
            "dimensions": ("INVALID_DIMENSIONS", {"width": 1080}),
            "duration": ("INVALID_DURATION", {"duration_seconds": 6}),
            "bitrate": ("LOW_VIDEO_BITRATE", {"video_bitrate_bps": 2_499_999}),
            "audio": ("SILENT_AUDIO", {"audio_peak": 0}),
            "decode": ("UNDECODABLE_VIDEO", {"decodable": False}),
            "subtitles": ("INVALID_SUBTITLES", {"subtitle_track": None}),
        }
        for name, (code, overrides) in cases.items():
            with self.subTest(name=name):
                result = run_quality_checks(valid_probe(**overrides), OutputSpec())
                self.assertEqual(result.decision.value, "FAIL")
                self.assertIn(code, {failure.code for failure in result.failures})

    def test_visual_signals_are_exposed_for_human_review(self):
        result = run_quality_checks(
            valid_probe(
                flicker_detected=True,
                subject_unstable=True,
                lip_sync_ok=False,
                subtitle_ocr_errors=("错别字",),
                timeline_anomalies=("cue-2 overlaps cue-3",),
            ),
            OutputSpec(),
        )
        self.assertEqual(result.decision.value, "PASS")
        self.assertEqual(
            result.human_review_flags,
            (
                "flicker_detected",
                "subject_unstable",
                "lip_sync_mismatch",
                "subtitle_ocr_error:错别字",
                "timeline_anomaly:cue-2 overlaps cue-3",
            ),
        )


if __name__ == "__main__":
    unittest.main()
