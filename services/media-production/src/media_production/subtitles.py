from __future__ import annotations

import re

from .models import SubtitleCue, SubtitleTrack


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def build_simplified_chinese_subtitles(
    script: str,
    *,
    duration_seconds: float,
    burned_in: bool = True,
) -> SubtitleTrack:
    """Create deterministic bottom subtitles for a short script."""

    text = " ".join(script.split())
    if not text:
        return SubtitleTrack("zh-CN", (), burned_in)

    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    if not sentences:
        sentences = [text]

    duration_ms = max(1, round(duration_seconds * 1000))
    step = duration_ms / len(sentences)
    cues = tuple(
        SubtitleCue(
            index=index,
            start_ms=round((index - 1) * step),
            end_ms=round(index * step),
            text=sentence,
        )
        for index, sentence in enumerate(sentences, start=1)
    )
    return SubtitleTrack("zh-CN", cues, burned_in)


def validate_subtitles(track: SubtitleTrack | None, duration_seconds: float) -> list[str]:
    if track is None:
        return ["subtitle track is missing"]
    if track.language != "zh-CN":
        return ["subtitle language must be zh-CN"]
    if not track.burned_in:
        return ["subtitles are not burned in"]
    if not track.cues:
        return ["subtitle track has no cues"]

    duration_ms = round(duration_seconds * 1000)
    errors: list[str] = []
    previous_end = 0
    for cue in track.cues:
        if not cue.text.strip():
            errors.append(f"subtitle cue {cue.index} is empty")
        if cue.start_ms < previous_end or cue.end_ms <= cue.start_ms:
            errors.append(f"subtitle cue {cue.index} has invalid timing")
        if cue.end_ms > duration_ms:
            errors.append(f"subtitle cue {cue.index} exceeds video duration")
        previous_end = cue.end_ms
    return errors
