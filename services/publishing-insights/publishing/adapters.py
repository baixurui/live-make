import hashlib
from typing import Protocol
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from .models import DependencyUnavailable, MetricValues, PublishResult, TaskContext


class TaskSource(Protocol):
    def get(self, task_id: str) -> TaskContext: ...


class Publisher(Protocol):
    def publish(self, key: str, attempt: int, seed: str) -> PublishResult: ...


def digest(*parts: str) -> int:
    # Length-prefixing keeps tuples unambiguous even if identifiers contain separators.
    payload = "".join(f"{len(part)}:{part}" for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


class SimulatedPublisher:
    def publish(self, key: str, attempt: int, seed: str) -> PublishResult:
        if digest("publish-v1", seed, key, str(attempt)) % 10 == 0:
            return PublishResult(success=False, error_code="SIMULATED_PLATFORM_FAILURE")
        return PublishResult(success=True, platform_post_id=f"sim_{digest('post-v1', seed, key):064x}")


def simulate_metrics(context: TaskContext, key: str, seed: str, stage: int) -> MetricValues:
    qc = context.qc_score if context.qc_score is not None else 50
    review = context.review_score if context.review_score is not None else 50
    quality = (qc + review) / 200
    variation = 100 + digest("audience-v1", seed, key) % 9901
    plays = int(variation * (0.5 + quality)) * {3600: 1, 86400: 3, 604800: 7}[stage]
    rates = {
        "likes": (20, 81), "comments": (1, 15), "favorites": (2, 29),
        "shares": (1, 20), "followers": (1, 10),
    }
    return MetricValues(plays=plays, **{
        field: plays * (minimum + digest("rate-v1", seed, key, field) % spread) // 1000
        for field, (minimum, spread) in rates.items()
    })


class HttpTaskSource:
    def __init__(self, base_url: str, token: str):
        if not token or not base_url.startswith(("http://", "https://")):
            raise ValueError("business API URL and token are required")
        self.base_url = base_url.rstrip("/")
        self.token = token

    def get(self, task_id: str) -> TaskContext:
        try:
            response = httpx.get(
                f"{self.base_url}/api/v1/internal/tasks/{quote(task_id, safe='')}/publishing-context",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5, follow_redirects=False,
            )
            response.raise_for_status()
            context = TaskContext.model_validate_json(response.content)
            if context.task_id != task_id:
                raise DependencyUnavailable("business API returned another task")
            return context
        except (httpx.HTTPError, ValidationError, ValueError) as error:
            raise DependencyUnavailable("business API unavailable or invalid context") from error
