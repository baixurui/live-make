from __future__ import annotations

from .models import MediaProbe, OutputSpec, QCDecision, QCFailure, QCResult
from .subtitles import validate_subtitles


def run_quality_checks(probe: MediaProbe, output_spec: OutputSpec) -> QCResult:
    failures: list[QCFailure] = []
    failures.extend(QCFailure("INVALID_OUTPUT_SPEC", error) for error in output_spec.validate())

    if probe.width != 720 or probe.height != 1280:
        failures.append(QCFailure("INVALID_DIMENSIONS", "video must be 720x1280"))
    if probe.container.lower() != "mp4":
        failures.append(QCFailure("INVALID_CONTAINER", "video container must be MP4"))
    if probe.video_codec.lower() not in {"h264", "avc1"}:
        failures.append(QCFailure("INVALID_VIDEO_CODEC", "video codec must be H.264"))
    if probe.audio_codec.lower() != "aac":
        failures.append(QCFailure("INVALID_AUDIO_CODEC", "audio codec must be AAC"))
    if abs(probe.fps - 25) > 0.01:
        failures.append(QCFailure("INVALID_FPS", "video must be 25fps"))
    if probe.video_bitrate_bps < 2_500_000:
        failures.append(QCFailure("LOW_VIDEO_BITRATE", "video bitrate must be at least 2.5Mbps"))
    if not 7 <= probe.duration_seconds <= 15:
        failures.append(QCFailure("INVALID_DURATION", "video duration must be 7-15 seconds"))
    if probe.audio_peak <= 0:
        failures.append(QCFailure("SILENT_AUDIO", "audio track is silent"))
    if not probe.decodable:
        failures.append(QCFailure("UNDECODABLE_VIDEO", "video cannot be decoded"))
    failures.extend(
        QCFailure("INVALID_SUBTITLES", error)
        for error in validate_subtitles(probe.subtitle_track, probe.duration_seconds)
    )

    human_review_flags: list[str] = []
    if probe.flicker_detected:
        human_review_flags.append("flicker_detected")
    if probe.subject_unstable:
        human_review_flags.append("subject_unstable")
    if not probe.lip_sync_ok:
        human_review_flags.append("lip_sync_mismatch")
    human_review_flags.extend(f"subtitle_ocr_error:{error}" for error in probe.subtitle_ocr_errors)
    human_review_flags.extend(f"timeline_anomaly:{error}" for error in probe.timeline_anomalies)

    return QCResult(
        decision=QCDecision.FAIL if failures else QCDecision.PASS,
        failures=tuple(failures),
        human_review_flags=tuple(human_review_flags),
        measurements={
            "width": probe.width,
            "height": probe.height,
            "fps": probe.fps,
            "video_bitrate_bps": probe.video_bitrate_bps,
            "duration_seconds": probe.duration_seconds,
        },
    )
