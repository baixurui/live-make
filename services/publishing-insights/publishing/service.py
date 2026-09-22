from datetime import datetime
import json
import logging
import sqlite3
from uuid import uuid4

from .adapters import Publisher, TaskSource
from .models import (DependencyUnavailable, DomainError, PublishRequest, Receipt,
                     SHANGHAI, TaskContext, stamp, utc_now)
from .storage import Store


logger = logging.getLogger(__name__)


def fingerprint(request: PublishRequest) -> str:
    data = request.data.model_dump(mode="json", exclude_none=True)
    data["scheduled_at"] = stamp(request.data.scheduled_at)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def eligible(request: PublishRequest, context: TaskContext, now: datetime):
    if context.task_id != request.data.task_id:
        raise DomainError("TASK_MISMATCH")
    if context.status not in {"SCHEDULED", "PUBLISHING"}:
        raise DomainError("TASK_NOT_SCHEDULED")
    if not (context.script_approved and context.video_approved and context.qc_passed):
        raise DomainError("APPROVAL_REQUIRED")
    if context.risk_level == "BLOCKED":
        raise DomainError("RISK_BLOCKED")
    if context.media_version != context.approved_media_version:
        raise DomainError("APPROVED_VERSION_MISMATCH")
    if request.data.account_id is not None and request.data.account_id != context.account_id:
        raise DomainError("ACCOUNT_MISMATCH")
    if request.data.media_version is not None and request.data.media_version != context.media_version:
        raise DomainError("MEDIA_VERSION_MISMATCH")
    scheduled = context.scheduled_at.astimezone(SHANGHAI)
    if request.data.scheduled_at != context.scheduled_at:
        raise DomainError("SCHEDULE_MISMATCH")
    if (scheduled.hour, scheduled.minute, scheduled.second, scheduled.microsecond) != (12, 0, 0, 0):
        raise DomainError("INVALID_SCHEDULE")
    if now < context.scheduled_at:
        raise DomainError("NOT_DUE")
    if now.astimezone(SHANGHAI).date() != scheduled.date():
        raise DomainError("SCHEDULE_EXPIRED")


class PublishingService:
    def __init__(self, store: Store, source: TaskSource, publisher: Publisher,
                 seed: str, clock=utc_now):
        self.store, self.source, self.publisher = store, source, publisher
        self.seed, self.clock = seed, clock

    def submit(self, request: PublishRequest) -> Receipt:
        key = request.idempotency_key
        with self.store.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM publications WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint(request):
                    raise DomainError("IDEMPOTENCY_CONFLICT")
            else:
                context = self.source.get(request.data.task_id)
                now = self.clock()
                eligible(request, context, now)
                try:
                    connection.execute(
                        """INSERT INTO publications
                        (idempotency_key,receipt_id,task_id,account_id,scheduled_day,request_json,
                         fingerprint,context_json,seed,status,accepted_at)
                        VALUES (?,?,?,?,?,?,?,?,?,'PROCESSING',?)""",
                        (key, request.data.receipt_id, context.task_id, context.account_id,
                         context.scheduled_at.astimezone(SHANGHAI).date().isoformat(),
                         request.model_dump_json(), fingerprint(request), context.model_dump_json(),
                         self.seed, stamp(now)),
                    )
                except sqlite3.IntegrityError as error:
                    raise DomainError("PUBLICATION_ALREADY_RESERVED") from error
        self.process(key)
        return self.receipt(request.data.receipt_id)

    def process(self, key: str):
        # Each attempt commits separately. Other workers can recover between attempts,
        # but the database write lock serializes the actual simulated operation.
        for _ in range(2):
            with self.store.transaction() as connection:
                row = connection.execute(
                    "SELECT * FROM publications WHERE idempotency_key=?", (key,)
                ).fetchone()
                if row is None or row["status"] != "PROCESSING":
                    return
                request = PublishRequest.model_validate_json(row["request_json"])
                original = TaskContext.model_validate_json(row["context_json"])
                try:
                    context = self.source.get(row["task_id"])
                    now = self.clock()
                    eligible(request, context, now)
                    if (context.account_id, context.media_version, context.scheduled_at) != (
                        original.account_id, original.media_version, original.scheduled_at
                    ):
                        raise DomainError("BUSINESS_CONTEXT_CHANGED")
                except DependencyUnavailable:
                    return  # No publishing attempt consumed; background worker can recover.
                except DomainError as error:
                    self._finish(connection, row, "ABORTED", self.clock(), error_code=error.code)
                    return
                attempt = row["attempt_count"] + 1
                if attempt > 2:
                    raise RuntimeError("processing publication exhausted its attempts")
                result = self.publisher.publish(key, attempt, row["seed"])
                if result.success and not result.platform_post_id:
                    raise ValueError("successful publisher result requires a platform post ID")
                connection.execute(
                    "INSERT INTO attempts VALUES (?,?,?,?,?)",
                    (key, attempt, stamp(now), int(result.success), result.error_code),
                )
                connection.execute(
                    "UPDATE publications SET attempt_count=? WHERE idempotency_key=?", (attempt, key)
                )
                if result.success:
                    self._finish(connection, row, "SUCCEEDED", now,
                                 platform_post_id=result.platform_post_id, attempt=attempt)
                    return
                if attempt == 2:
                    self._finish(connection, row, "FAILED", now,
                                 error_code=result.error_code or "PUBLISH_FAILED", attempt=attempt)
                    return

    def _finish(self, connection, row, status, now, error_code=None,
                platform_post_id=None, attempt=None):
        published_at = stamp(now) if status == "SUCCEEDED" else None
        connection.execute(
            """UPDATE publications SET status=?, published_at=?, platform_post_id=?, error_code=?
               WHERE idempotency_key=?""",
            (status, published_at, platform_post_id, error_code, row["idempotency_key"]),
        )
        request = json.loads(row["request_json"])
        success = status == "SUCCEEDED"
        data = {"task_id": row["task_id"], "receipt_id": row["receipt_id"]}
        if success:
            data.update(platform_post_id=platform_post_id, published_at=published_at)
        else:
            data.update(failed_step="publishing", error_code=error_code, retryable=False,
                        request_key=row["idempotency_key"],
                        attempt=attempt if attempt is not None else row["attempt_count"])
        event = {
            "event_id": str(uuid4()),
            "event_type": "task.published.v1" if success else "task.failed.v1",
            "occurred_at": stamp(now), "correlation_id": request["correlation_id"],
            "idempotency_key": row["idempotency_key"], "data": data,
        }
        connection.execute(
            "INSERT INTO outbox(event_id,receipt_id,payload,created_at) VALUES (?,?,?,?)",
            (event["event_id"], row["receipt_id"], json.dumps(event), stamp(now)),
        )

    def receipt(self, receipt_id: str) -> Receipt:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM publications WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            if row is None:
                raise DomainError("RECEIPT_NOT_FOUND", 404)
            attempts = connection.execute(
                "SELECT * FROM attempts WHERE idempotency_key=? ORDER BY number",
                (row["idempotency_key"],),
            ).fetchall()
        context = json.loads(row["context_json"])
        return Receipt.model_validate_json(json.dumps({
            "receipt_id": receipt_id, "task_id": row["task_id"], "account_id": row["account_id"],
            "media_version": context["media_version"], "idempotency_key": row["idempotency_key"],
            "status": row["status"], "accepted_at": row["accepted_at"],
            "published_at": row["published_at"], "platform_post_id": row["platform_post_id"],
            "error_code": row["error_code"], "attempts": [
                {"number": a["number"], "attempted_at": a["attempted_at"],
                 "success": bool(a["success"]), "error_code": a["error_code"]} for a in attempts
            ],
        }))

    def events(self, limit: int = 100) -> list[dict]:
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM outbox WHERE acked_at IS NULL ORDER BY created_at,event_id LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def acknowledge(self, event_id: str):
        with self.store.transaction() as connection:
            row = connection.execute("SELECT event_id FROM outbox WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                raise DomainError("EVENT_NOT_FOUND", 404)
            connection.execute("UPDATE outbox SET acked_at=COALESCE(acked_at,?) WHERE event_id=?",
                               (stamp(self.clock()), event_id))

    def recover(self):
        with self.store.connection() as connection:
            keys = [row[0] for row in connection.execute(
                "SELECT idempotency_key FROM publications WHERE status='PROCESSING' ORDER BY accepted_at"
            )]
        for key in keys:
            try:
                self.process(key)
            except Exception:
                logger.exception("publication recovery failed")
