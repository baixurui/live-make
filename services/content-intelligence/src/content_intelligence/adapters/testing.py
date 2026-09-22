from ..models import SearchBatch
from ..ports import ProviderError


class FakeSearchProvider:
    def __init__(self, outcome: SearchBatch | ProviderError) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int) -> SearchBatch:
        self.calls.append((query, limit))
        if isinstance(self.outcome, ProviderError):
            raise ProviderError(self.outcome.retryable, self.outcome.call)
        return self.outcome
