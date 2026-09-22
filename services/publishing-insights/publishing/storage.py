from contextlib import contextmanager
from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS publications (
    idempotency_key TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    scheduled_day TEXT NOT NULL,
    request_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    context_json TEXT NOT NULL,
    seed TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PROCESSING','SUCCEEDED','FAILED','ABORTED')),
    accepted_at TEXT NOT NULL,
    published_at TEXT,
    platform_post_id TEXT UNIQUE,
    error_code TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 2),
    UNIQUE(account_id, scheduled_day)
);
CREATE TABLE IF NOT EXISTS attempts (
    idempotency_key TEXT NOT NULL REFERENCES publications(idempotency_key),
    number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 2),
    attempted_at TEXT NOT NULL,
    success INTEGER NOT NULL,
    error_code TEXT,
    PRIMARY KEY(idempotency_key, number)
);
CREATE TABLE IF NOT EXISTS outbox (
    event_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE REFERENCES publications(receipt_id),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acked_at TEXT
);
CREATE TABLE IF NOT EXISTS snapshot_stages (
    receipt_id TEXT NOT NULL REFERENCES publications(receipt_id),
    stage INTEGER NOT NULL CHECK(stage IN (3600,86400,604800)),
    scheduled_at TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY(receipt_id, stage)
);
CREATE TABLE IF NOT EXISTS snapshots (
    receipt_id TEXT NOT NULL,
    stage INTEGER NOT NULL,
    sampled_at TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    degraded INTEGER NOT NULL,
    PRIMARY KEY(receipt_id, stage),
    FOREIGN KEY(receipt_id, stage) REFERENCES snapshot_stages(receipt_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_publications_account_time ON publications(account_id, accepted_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_expiration ON snapshots(generated_at);
"""


class Store:
    def __init__(self, path: str | Path):
        if str(path) == ":memory:":
            raise ValueError("use a file database: each operation uses its own connection")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
