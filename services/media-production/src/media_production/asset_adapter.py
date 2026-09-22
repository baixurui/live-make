from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .models import AssetRecord


def resolve_video_uri(result: Mapping[str, Any], assets: Iterable[AssetRecord]) -> str:
    """Resolve the rendered video URI for the workflow/business API adapter."""

    asset_ids = set(result.get("data", {}).get("asset_ids", ()))
    for asset in assets:
        if asset.asset_id in asset_ids and asset.asset_type == "video":
            return asset.uri
    return ""


def serialize_asset(asset: AssetRecord) -> dict[str, Any]:
    """Convert an asset record to the business API metadata shape."""

    return {
        "id": asset.asset_id,
        "kind": asset.asset_type,
        "uri": asset.uri,
        "metadata": {
            "supplier": asset.supplier,
            "model": asset.model,
            "parameters": dict(asset.parameters),
            "cost": asset.cost,
            "duration_ms": asset.duration_ms,
            "version": asset.version,
            "license_status": asset.license_status,
            "upstream_asset_ids": list(asset.upstream_asset_ids),
            "downstream_asset_ids": list(asset.downstream_asset_ids),
            "created_at": asset.created_at,
        },
    }
