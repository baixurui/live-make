from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .contract_adapter import build_qc_completed_event
from .mock_provider import DeterministicMockProvider
from .models import OutputSpec
from .providers import MediaProvider
from .qc import run_quality_checks
from .subtitles import build_simplified_chinese_subtitles


class MediaProductionPipeline:
    def __init__(self, provider: MediaProvider | None = None, *, avatar_id: str = "avatar-001") -> None:
        self.provider = provider or DeterministicMockProvider()
        self.avatar_id = avatar_id
        self._completed: dict[str, dict[str, Any]] = {}
        self._assets: dict[str, Any] = {}
        self._upstream_voice_cache: dict[str, Any] = {}

    def handle_media_requested(self, event: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_request(event)
        idempotency_key = str(event["idempotency_key"])
        if idempotency_key in self._completed:
            return self._completed[idempotency_key]

        data = event["data"]
        output_spec = OutputSpec.from_mapping(data.get("output_spec"))
        script = str(data.get("script", "今日为你介绍一个值得关注的主题。"))
        candidates = self.provider.avatar_candidates()
        if len(candidates) != 4:
            raise ValueError("media production requires exactly four avatar candidates")
        if self.avatar_id not in {candidate.asset_id for candidate in candidates}:
            raise ValueError(f"selected avatar is not one of the four candidates: {self.avatar_id}")
        avatar = self.provider.select_avatar(self.avatar_id)
        voice_version = str(data.get("voice_version", "voice-v1"))
        voice_cache_key = ":".join((str(data["task_id"]), str(data["script_version"]), voice_version, script))
        voice = self._upstream_voice_cache.get(voice_cache_key)
        if voice is None:
            voice = self.provider.synthesize_voice(script, voice_version)
            self._upstream_voice_cache[voice_cache_key] = voice
        estimated_duration = max(output_spec.duration_min_seconds, min(output_spec.duration_max_seconds, len(script) * 0.18))
        subtitles = build_simplified_chinese_subtitles(
            script,
            duration_seconds=estimated_duration,
            burned_in=output_spec.subtitles_burned_in,
        )
        rendered = self.provider.render_video(script, avatar, voice, subtitles, output_spec)
        result = run_quality_checks(rendered.probe, output_spec)
        video = rendered.asset
        self._assets[avatar.asset_id] = replace(avatar, downstream_asset_ids=(video.asset_id,))
        self._assets[voice.asset_id] = replace(voice, downstream_asset_ids=(video.asset_id,))
        self._assets[video.asset_id] = video
        response = build_qc_completed_event(event, result, (avatar, voice, video))
        self._completed[idempotency_key] = response
        return response

    @property
    def asset_history(self) -> tuple[Any, ...]:
        """Return recorded assets for audit/history consumers."""

        return tuple(self._assets.values())

    @staticmethod
    def _validate_request(event: Mapping[str, Any]) -> None:
        required = {"event_id", "event_type", "occurred_at", "correlation_id", "idempotency_key", "data"}
        missing = required.difference(event)
        if missing:
            raise ValueError(f"missing event fields: {', '.join(sorted(missing))}")
        if event["event_type"] != "task.media_requested.v1":
            raise ValueError("event_type must be task.media_requested.v1")
        data = event["data"]
        for field in ("task_id", "script_version", "media_version"):
            if field not in data:
                raise ValueError(f"missing request data field: {field}")
