from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
WEEK = timedelta(days=7)
Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"\S")]
Version = Annotated[int, Field(ge=1, strict=True)]


def utc_now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("an ISO 8601 datetime is required")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone is required")
    return value.astimezone(UTC)


def stamp(value: datetime) -> str:
    return aware(value).isoformat(timespec="microseconds")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PublishData(StrictModel):
    task_id: Identifier
    receipt_id: Identifier
    scheduled_at: datetime
    account_id: Identifier | None = None
    media_version: Version | None = None

    _aware = field_validator("scheduled_at", mode="before")(aware)


class PublishRequest(StrictModel):
    event_id: Identifier
    event_type: Literal["task.publish_requested.v1"]
    occurred_at: datetime
    correlation_id: Identifier
    idempotency_key: Identifier
    data: PublishData

    _aware = field_validator("occurred_at", mode="before")(aware)


class TaskContext(StrictModel):
    task_id: Identifier
    account_id: Identifier
    status: Literal["DISCOVERED", "TOPIC_SELECTED", "SCRIPT_GENERATING", "SCRIPT_AUTO_REVIEWING",
                    "SCRIPT_PENDING_APPROVAL", "SCRIPT_APPROVED", "SCRIPT_REJECTED", "MEDIA_GENERATING",
                    "MEDIA_QC_RUNNING", "VIDEO_PENDING_APPROVAL", "VIDEO_APPROVED", "VIDEO_REJECTED",
                    "SCHEDULED", "PUBLISHING", "PUBLISHED", "PAUSED", "FAILED", "CANCELLED"]
    scheduled_at: datetime
    media_version: Version
    approved_media_version: Version
    script_approved: bool
    video_approved: bool
    qc_passed: bool
    risk_level: Literal["LOW", "HIGH", "BLOCKED"]
    qc_score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    review_score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None

    _aware = field_validator("scheduled_at", mode="before")(aware)


class PublishResult(StrictModel):
    success: bool
    platform_post_id: str | None = None
    error_code: str | None = None


class Attempt(StrictModel):
    number: int
    attempted_at: datetime
    success: bool
    error_code: str | None


class Receipt(StrictModel):
    receipt_id: str
    task_id: str
    account_id: str
    media_version: Version
    idempotency_key: str
    status: Literal["PROCESSING", "SUCCEEDED", "FAILED", "ABORTED"]
    accepted_at: datetime
    published_at: datetime | None
    platform_post_id: str | None
    error_code: str | None
    attempts: list[Attempt]


class PublishedData(StrictModel):
    task_id: str
    receipt_id: str
    platform_post_id: str
    published_at: datetime


class FailedData(StrictModel):
    task_id: str
    failed_step: Literal["publishing"]
    error_code: str
    retryable: Literal[False]
    attempt: Annotated[int, Field(ge=0, le=2)]
    receipt_id: str
    request_key: str


class PublishedEvent(StrictModel):
    event_id: str
    event_type: Literal["task.published.v1"]
    occurred_at: datetime
    correlation_id: str
    idempotency_key: str
    data: PublishedData


class FailedEvent(StrictModel):
    event_id: str
    event_type: Literal["task.failed.v1"]
    occurred_at: datetime
    correlation_id: str
    idempotency_key: str
    data: FailedData


class MetricValues(StrictModel):
    plays: int = 0
    likes: int = 0
    comments: int = 0
    favorites: int = 0
    shares: int = 0
    followers: int = 0


class OutcomeCounts(StrictModel):
    succeeded: int = 0
    failed: int = 0
    processing: int = 0
    aborted: int = 0
    success_rate: float | None = None


class DailyMetrics(StrictModel):
    date: str
    metrics: MetricValues
    published_count: int = 0
    snapshot_count: int = 0


class DailyOutcomes(StrictModel):
    date: str
    outcomes: OutcomeCounts


class MetricsResponse(StrictModel):
    account_id: str
    window_start: datetime
    window_end: datetime
    as_of: datetime
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    metrics: MetricValues
    published_count: int
    snapshot_count: int
    outcomes: OutcomeCounts
    daily_metrics: list[DailyMetrics]
    daily_outcomes: list[DailyOutcomes]


class ErrorResponse(StrictModel):
    code: str


class DomainError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


class DependencyUnavailable(Exception):
    """Trusted business state cannot currently be verified."""
