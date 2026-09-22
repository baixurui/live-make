from datetime import date
import json
from uuid import NAMESPACE_URL, uuid5

from .models import Recommendation, RecommendationBatch, SearchResult, Topic
from .ports import SearchProvider


class RecommendationService:
    def __init__(self, provider: SearchProvider) -> None:
        self.provider = provider

    def recommend(self, topic: Topic, scan_date: date) -> RecommendationBatch:
        if not topic.enabled:
            return RecommendationBatch(topic.topic_id, scan_date, (), None)
        batch = self.provider.search(" ".join(topic.keywords), limit=10)
        ordered = sorted(
            batch.results,
            key=lambda item: (
                -item.relevance,
                item.url,
                item.title,
                item.summary,
                item.source_time.isoformat() if item.source_time is not None else "",
            ),
        )
        unique: dict[str, SearchResult] = {}
        for item in ordered:
            if item.url not in unique:
                unique[item.url] = item
            if len(unique) == 10:
                break
        recommendations = []
        for rank, item in enumerate(unique.values(), start=1):
            identity = json.dumps(
                ["live-make-recommendation", topic.topic_id, scan_date.isoformat(), item.url],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            recommendations.append(
                Recommendation(
                    str(uuid5(NAMESPACE_URL, identity)),
                    topic.topic_id,
                    scan_date,
                    rank,
                    item,
                    batch.call.call_id,
                )
            )
        return RecommendationBatch(topic.topic_id, scan_date, tuple(recommendations), batch.call)
