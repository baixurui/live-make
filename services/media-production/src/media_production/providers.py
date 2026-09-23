from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import AssetRecord, MediaProbe, OutputSpec, SubtitleTrack


@dataclass(frozen=True)
class RenderedMedia:
    asset: AssetRecord
    probe: MediaProbe


class MediaProvider(Protocol):
    def avatar_candidates(self) -> tuple[AssetRecord, ...]: ...

    def select_avatar(self, candidate_id: str) -> AssetRecord: ...

    def synthesize_voice(self, script: str, voice_version: str) -> AssetRecord: ...

    def render_video(
        self,
        script: str,
        avatar: AssetRecord,
        voice: AssetRecord,
        subtitles: SubtitleTrack,
        output_spec: OutputSpec,
    ) -> RenderedMedia: ...
