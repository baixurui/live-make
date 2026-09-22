from datetime import datetime, timedelta
import json

from .adapters import simulate_metrics
from .models import (DailyMetrics, DailyOutcomes, MetricValues, MetricsResponse,
                     OutcomeCounts, SHANGHAI, TaskContext, WEEK, stamp, utc_now)
from .storage import Store


STAGES = (3600, 86400, 604800)


class Insights:
    def __init__(self, store: Store, clock=utc_now):
        self.store, self.clock = store, clock

    def maintain(self):
        now = self.clock()
        with self.store.transaction() as connection:
            rows = connection.execute("SELECT * FROM publications WHERE status='SUCCEEDED'").fetchall()
            for row in rows:
                published = datetime.fromisoformat(row["published_at"])
                context = TaskContext.model_validate_json(row["context_json"])
                for stage in STAGES:
                    sampled = published + timedelta(seconds=stage)
                    if sampled > now:
                        continue
                    inserted = connection.execute(
                        """INSERT OR IGNORE INTO snapshot_stages
                           (receipt_id,stage,scheduled_at,generated_at) VALUES (?,?,?,?)""",
                        (row["receipt_id"], stage, stamp(sampled), stamp(now)),
                    ).rowcount
                    if inserted:
                        metrics = simulate_metrics(context, row["idempotency_key"], row["seed"], stage)
                        connection.execute(
                            "INSERT INTO snapshots VALUES (?,?,?,?,?,?)",
                            (row["receipt_id"], stage, stamp(sampled), stamp(now), metrics.model_dump_json(),
                             int(context.qc_score is None or context.review_score is None)),
                        )
            connection.execute("DELETE FROM snapshots WHERE generated_at<=?", (stamp(now - WEEK),))

    def metrics(self, account_id: str) -> MetricsResponse:
        now = self.clock()
        start = now - WEEK
        date = start.astimezone(SHANGHAI).date()
        last = (now - timedelta(microseconds=1)).astimezone(SHANGHAI).date()
        daily_metrics, daily_outcomes = {}, {}
        while date <= last:
            day = date.isoformat()
            daily_metrics[day] = DailyMetrics(date=day, metrics=MetricValues())
            daily_outcomes[day] = DailyOutcomes(date=day, outcomes=OutcomeCounts())
            date += timedelta(days=1)
        total, outcomes = MetricValues(), OutcomeCounts()
        published_count = snapshot_count = 0
        with self.store.connection() as connection:
            # A consistent read snapshot covers both outcome and metric queries.
            connection.execute("BEGIN")
            accepted = connection.execute(
                "SELECT status,accepted_at FROM publications WHERE account_id=? AND accepted_at>=? AND accepted_at<?",
                (account_id, stamp(start), stamp(now)),
            ).fetchall()
            for row in accepted:
                field = {"SUCCEEDED": "succeeded", "FAILED": "failed", "PROCESSING": "processing",
                         "ABORTED": "aborted"}[row["status"]]
                day = datetime.fromisoformat(row["accepted_at"]).astimezone(SHANGHAI).date().isoformat()
                for target in (outcomes, daily_outcomes[day].outcomes):
                    setattr(target, field, getattr(target, field) + 1)
            publications = connection.execute(
                """SELECT receipt_id,published_at FROM publications
                   WHERE account_id=? AND status='SUCCEEDED' AND published_at>=? AND published_at<?""",
                (account_id, stamp(start), stamp(now)),
            ).fetchall()
            for row in publications:
                day = datetime.fromisoformat(row["published_at"]).astimezone(SHANGHAI).date().isoformat()
                daily = daily_metrics[day]
                published_count += 1
                daily.published_count += 1
                snapshot = connection.execute(
                    """SELECT metrics_json FROM snapshots WHERE receipt_id=? AND sampled_at<=?
                       AND generated_at<=? AND generated_at>? ORDER BY stage DESC LIMIT 1""",
                    (row["receipt_id"], stamp(now), stamp(now), stamp(start)),
                ).fetchone()
                if snapshot:
                    snapshot_count += 1
                    daily.snapshot_count += 1
                    values = json.loads(snapshot["metrics_json"])
                    for field, value in values.items():
                        setattr(total, field, getattr(total, field) + value)
                        setattr(daily.metrics, field, getattr(daily.metrics, field) + value)
        for target in (outcomes, *(item.outcomes for item in daily_outcomes.values())):
            denominator = target.succeeded + target.failed
            target.success_rate = target.succeeded / denominator if denominator else None
        return MetricsResponse(
            account_id=account_id, window_start=start, window_end=now, as_of=now,
            metrics=total, outcomes=outcomes, published_count=published_count,
            snapshot_count=snapshot_count, daily_metrics=list(daily_metrics.values()),
            daily_outcomes=list(daily_outcomes.values()),
        )
