from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from .models import AssetRecord, QCResult


def build_qc_completed_event(
    request: Mapping[str, Any],
    result: QCResult,
    assets: tuple[AssetRecord, ...],
) -> dict[str, Any]:
    """Build the shared envelope while provisional payload values are isolated."""

    data = request["data"]
    return {
        "event_id": str(uuid4()),
        "event_type": "task.media_qc_completed.v1",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "correlation_id": request["correlation_id"],
        "idempotency_key": f"{request['idempotency_key']}:qc",
        "data": {
            "task_id": data["task_id"],
            "media_version": data["media_version"],
            "asset_ids": [asset.asset_id for asset in assets],
            "qc_decision": result.decision.value,
            "failures": [
                {"code": failure.code, "message": failure.message, "hard_stop": failure.hard_stop}
                for failure in result.failures
            ],
            "human_review_flags": list(result.human_review_flags),
            "requires_human_review": bool(result.human_review_flags),
        },
    }
