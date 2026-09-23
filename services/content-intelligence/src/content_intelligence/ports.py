from typing import Protocol

from .models import CallRecord, SearchBatch


class ProviderError(RuntimeError):
    def __init__(self, retryable: bool, call: CallRecord) -> None:
        super().__init__("SEARCH_UNAVAILABLE")
        self.retryable = retryable
        self.call = call


class SearchProvider(Protocol):
    def search(self, query: str, limit: int) -> SearchBatch: ...
