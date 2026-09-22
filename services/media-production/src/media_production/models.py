from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class QCDecision(str, Enum):
    """Provisional QC values, isolated until the shared contract is aligned."""

    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class OutputSpec:
    width: int = 720
    height: int = 1280
    container: str = "mp4"
    video_codec: str = "h264"
    audio_codec: str = "aac"
    fps: int = 25
    video_bitrate_bps: int = 2_500_000
    duration_min_seconds: float = 7.0
    duration_max_seconds: float = 15.0
    subtitle_language: str = "zh-CN"
    subtitles_burned_in: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "OutputSpec":
        if value is None:
            return cls()
        aliases = {
            "video_bitrate": "video_bitrate_bps",
            "min_duration": "duration_min_seconds",
            "max_duration": "duration_max_seconds",
        }
        normalized = {aliases.get(key, key): item for key, item in value.items()}
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: item for key, item in normalized.items() if key in allowed})

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.width != 720 or self.height != 1280:
            errors.append("output dimensions must be 720x1280")
        if self.container.lower() != "mp4":
            errors.append("container must be mp4")
        if self.video_codec.lower() not in {"h264", "avc1"}:
            errors.append("video codec must be H.264")
        if self.audio_codec.lower() != "aac":
            errors.append("audio codec must be AAC")
        if self.fps != 25:
            errors.append("frame rate must be 25fps")
        if self.video_bitrate_bps < 2_500_000:
            errors.append("video bitrate must be at least 2.5Mbps")
        if self.duration_min_seconds < 7 or self.duration_max_seconds > 15:
            errors.append("duration bounds must be within 7-15 seconds")
        if self.duration_min_seconds > self.duration_max_seconds:
            errors.append("minimum duration cannot exceed maximum duration")
        if self.subtitle_language != "zh-CN":
            errors.append("subtitle language must be zh-CN")
        if not self.subtitles_burned_in:
            errors.append("subtitles must be burned into the video")
        return errors


@dataclass(frozen=True)
class AssetRecord:
    asset_id: str
    asset_type: str
    supplier: str
    model: str
    parameters: Mapping[str, Any]
    cost: float
    duration_ms: int
    version: str
    license_status: str
    upstream_asset_ids: tuple[str, ...] = ()
    downstream_asset_ids: tuple[str, ...] = ()
    created_at: str = ""


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class SubtitleTrack:
    language: str
    cues: tuple[SubtitleCue, ...]
    burned_in: bool


@dataclass(frozen=True)
class MediaProbe:
    width: int
    height: int
    container: str
    video_codec: str
    audio_codec: str
    fps: float
    video_bitrate_bps: int
    duration_seconds: float
    audio_peak: float
    decodable: bool
    subtitle_track: SubtitleTrack | None
    flicker_detected: bool = False
    subject_unstable: bool = False
    lip_sync_ok: bool = True
    subtitle_ocr_errors: tuple[str, ...] = ()
    timeline_anomalies: tuple[str, ...] = ()


@dataclass(frozen=True)
class QCFailure:
    code: str
    message: str
    hard_stop: bool = True


@dataclass(frozen=True)
class QCResult:
    decision: QCDecision
    failures: tuple[QCFailure, ...] = ()
    human_review_flags: tuple[str, ...] = ()
    measurements: Mapping[str, Any] = field(default_factory=dict)
