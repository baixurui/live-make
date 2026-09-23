from __future__ import annotations

from typing import Final


TASK_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "DISCOVERED",
        "TOPIC_SELECTED",
        "SCRIPT_GENERATING",
        "SCRIPT_AUTO_REVIEWING",
        "SCRIPT_PENDING_APPROVAL",
        "SCRIPT_APPROVED",
        "SCRIPT_REJECTED",
        "MEDIA_GENERATING",
        "MEDIA_QC_RUNNING",
        "VIDEO_PENDING_APPROVAL",
        "VIDEO_APPROVED",
        "VIDEO_REJECTED",
        "SCHEDULED",
        "PUBLISHING",
        "PUBLISHED",
        "PAUSED",
        "FAILED",
        "CANCELLED",
    }
)

ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "DISCOVERED": frozenset({"TOPIC_SELECTED", "CANCELLED"}),
    "TOPIC_SELECTED": frozenset({"SCRIPT_GENERATING", "CANCELLED"}),
    "SCRIPT_GENERATING": frozenset({"SCRIPT_AUTO_REVIEWING", "FAILED", "CANCELLED"}),
    "SCRIPT_AUTO_REVIEWING": frozenset({"SCRIPT_PENDING_APPROVAL", "SCRIPT_REJECTED", "FAILED", "CANCELLED"}),
    "SCRIPT_PENDING_APPROVAL": frozenset({"SCRIPT_APPROVED", "SCRIPT_REJECTED", "CANCELLED"}),
    "SCRIPT_APPROVED": frozenset({"MEDIA_GENERATING", "CANCELLED"}),
    "SCRIPT_REJECTED": frozenset({"SCRIPT_GENERATING", "CANCELLED"}),
    "MEDIA_GENERATING": frozenset({"MEDIA_QC_RUNNING", "FAILED", "CANCELLED"}),
    "MEDIA_QC_RUNNING": frozenset({"VIDEO_PENDING_APPROVAL", "VIDEO_REJECTED", "FAILED", "CANCELLED"}),
    "VIDEO_PENDING_APPROVAL": frozenset({"VIDEO_APPROVED", "VIDEO_REJECTED", "CANCELLED"}),
    "VIDEO_APPROVED": frozenset({"SCHEDULED", "CANCELLED"}),
    "VIDEO_REJECTED": frozenset({"SCRIPT_GENERATING", "MEDIA_GENERATING", "CANCELLED"}),
    "SCHEDULED": frozenset({"PUBLISHING", "CANCELLED"}),
    "PUBLISHING": frozenset({"PUBLISHED", "FAILED"}),
    "PUBLISHED": frozenset(),
    "PAUSED": frozenset({"SCHEDULED", "SCRIPT_GENERATING", "MEDIA_GENERATING", "SCRIPT_PENDING_APPROVAL", "VIDEO_PENDING_APPROVAL", "CANCELLED"}),
    "FAILED": frozenset({"SCRIPT_GENERATING", "MEDIA_GENERATING", "PUBLISHING", "CANCELLED"}),
    "CANCELLED": frozenset(),
}

for status in TASK_STATUSES - {"PUBLISHED", "CANCELLED", "PAUSED"}:
    ALLOWED_TRANSITIONS[status] = ALLOWED_TRANSITIONS[status] | {"PAUSED"}


class InvalidTransition(ValueError):
    """Raised when a workflow requests an invalid task transition."""


def validate_transition(current: str, target: str) -> None:
    if current not in TASK_STATUSES or target not in TASK_STATUSES:
        raise InvalidTransition(f"unknown task status: {current} -> {target}")
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"invalid task transition: {current} -> {target}")
