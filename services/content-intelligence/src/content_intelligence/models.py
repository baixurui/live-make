from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite
from urllib.parse import urlsplit


def require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("Timestamp must have a timezone")


@dataclass(frozen=True)
class Topic:
    topic_id: str
    name: str
    keywords: tuple[str, ...]
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.topic_id.strip() or not self.name.strip():
            raise ValueError("Topic ID and name must not be empty")
        if not isinstance(self.keywords, tuple):
            raise ValueError("Keywords must be an immutable tuple")
        if not self.keywords or any(not keyword.strip() for keyword in self.keywords):
            raise ValueError("Keywords must not be empty")


@dataclass(frozen=True)
class CallRecord:
    call_id: str
    provider: str
    started_at: datetime
    finished_at: datetime
    model: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: Decimal | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if not self.call_id.strip() or not self.provider.strip():
            raise ValueError("Call ID and provider must not be empty")
        require_aware(self.started_at)
        require_aware(self.finished_at)
        started_at = self.started_at.astimezone(timezone.utc)
        finished_at = self.finished_at.astimezone(timezone.utc)
        if finished_at < started_at:
            raise ValueError("Call finish precedes start")
        for count in (self.input_tokens, self.output_tokens):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("Token count must be a non-negative integer")
        if (self.cost is None) != (self.currency is None):
            raise ValueError("Cost and currency must be supplied together")
        if self.cost is not None:
            if not self.cost.is_finite() or self.cost < 0:
                raise ValueError("Cost must be finite and non-negative")
            if not self.currency or len(self.currency) != 3 or not self.currency.isalpha():
                raise ValueError("Currency must be a three-letter code")

    @property
    def latency_ms(self) -> int:
        elapsed = self.finished_at.astimezone(timezone.utc) - self.started_at.astimezone(timezone.utc)
        return elapsed // timedelta(milliseconds=1)


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    summary: str
    relevance: float
    source_time: datetime | None = None

    def __post_init__(self) -> None:
        try:
            address = urlsplit(self.url)
            hostname = address.hostname
        except ValueError:
            raise ValueError("Source URL must be a valid HTTP(S) address") from None
        if address.scheme not in ("http", "https") or not hostname:
            raise ValueError("Source URL must use HTTP(S) with a host")
        if address.username is not None or address.password is not None:
            raise ValueError("Source URL must not include credentials")
        if not self.title.strip() or not self.summary.strip():
            raise ValueError("Source title and summary must not be empty")
        if isinstance(self.relevance, bool) or not isfinite(self.relevance):
            raise ValueError("Relevance must be finite")
        if self.source_time is not None:
            require_aware(self.source_time)


@dataclass(frozen=True)
class SearchBatch:
    results: tuple[SearchResult, ...]
    call: CallRecord

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple):
            raise ValueError("Search results must be an immutable tuple")


@dataclass(frozen=True)
class Recommendation:
    recommendation_id: str
    topic_id: str
    scan_date: date
    rank: int
    source: SearchResult
    call_id: str


@dataclass(frozen=True)
class RecommendationBatch:
    topic_id: str
    scan_date: date
    recommendations: tuple[Recommendation, ...]
    call: CallRecord | None
