from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .models import AssetRecord, MediaProbe, OutputSpec, SubtitleTrack
from .providers import MediaProvider, RenderedMedia


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _created_at() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeterministicMockProvider(MediaProvider):
    """Local provider for tests and development without vendor credentials."""

    def __init__(self) -> None:
        self._avatars = tuple(
            AssetRecord(
                asset_id=f"avatar-{index:03d}",
                asset_type="avatar",
                supplier="mock",
                model="fixed-digital-human",
                parameters={"candidate": index},
                cost=0.0,
                duration_ms=0,
                version=f"v{index}",
                license_status="verified",
                created_at=_created_at(),
            )
            for index in range(1, 5)
        )

    def avatar_candidates(self) -> tuple[AssetRecord, ...]:
        return self._avatars

    def select_avatar(self, candidate_id: str) -> AssetRecord:
        for avatar in self._avatars:
            if avatar.asset_id == candidate_id:
                return avatar
        raise ValueError(f"unknown avatar candidate: {candidate_id}")

    def synthesize_voice(self, script: str, voice_version: str) -> AssetRecord:
        return AssetRecord(
            asset_id=_stable_id("voice", script, voice_version),
            asset_type="voice",
            supplier="mock",
            model="commercial-tts",
            parameters={"voice_version": voice_version, "language": "zh-CN"},
            cost=0.0,
            duration_ms=max(7000, min(15000, len(script) * 180)),
            version=voice_version,
            license_status="verified",
            created_at=_created_at(),
        )

    def render_video(
        self,
        script: str,
        avatar: AssetRecord,
        voice: AssetRecord,
        subtitles: SubtitleTrack,
        output_spec: OutputSpec,
    ) -> RenderedMedia:
        duration = max(output_spec.duration_min_seconds, min(output_spec.duration_max_seconds, len(script) * 0.18))
        asset = AssetRecord(
            asset_id=_stable_id("video", script, avatar.version, voice.version),
            asset_type="video",
            supplier="mock",
            model="deterministic-renderer",
            parameters={
                "avatar_version": avatar.version,
                "voice_version": voice.version,
                "subtitle_count": len(subtitles.cues),
            },
            cost=0.0,
            duration_ms=round(duration * 1000),
            version="v1",
            license_status="verified",
            upstream_asset_ids=(avatar.asset_id, voice.asset_id),
            created_at=_created_at(),
        )
        probe = MediaProbe(
            width=output_spec.width,
            height=output_spec.height,
            container=output_spec.container,
            video_codec=output_spec.video_codec,
            audio_codec=output_spec.audio_codec,
            fps=output_spec.fps,
            video_bitrate_bps=output_spec.video_bitrate_bps,
            duration_seconds=duration,
            audio_peak=0.25,
            decodable=True,
            subtitle_track=subtitles,
            flicker_detected=False,
            subject_unstable=False,
            lip_sync_ok=True,
        )
        return RenderedMedia(asset=asset, probe=probe)
