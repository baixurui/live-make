from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .security import hash_password, verify_password
from .state_machine import validate_transition


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    keywords TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    topic_id TEXT REFERENCES topics(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    current_script_version INTEGER,
    current_media_version INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id),
    recommendation_date TEXT NOT NULL,
    score REAL NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS script_versions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);
CREATE TABLE IF NOT EXISTS media_versions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    version INTEGER NOT NULL,
    uri TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    kind TEXT NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    kind TEXT NOT NULL,
    uri TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_receipts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    status TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id TEXT PRIMARY KEY,
    snapshot_date TEXT NOT NULL UNIQUE,
    metrics TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    actor_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_logs_resource_idx ON audit_logs(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS audit_logs_created_at_idx ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status);
"""


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(approvals)")}
        if "script_version" not in columns:
            self.connection.execute("ALTER TABLE approvals ADD COLUMN script_version INTEGER")
        if "media_version" not in columns:
            self.connection.execute("ALTER TABLE approvals ADD COLUMN media_version INTEGER")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create_account(self, username: str, password: str, is_admin: bool = False) -> dict[str, Any]:
        now = _now()
        account = {"id": _id(), "username": username, "enabled": True, "is_admin": is_admin, "created_at": now, "updated_at": now}
        self.connection.execute(
            "INSERT INTO accounts(id, username, password_hash, enabled, is_admin, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account["id"], username, hash_password(password), 1, int(is_admin), now, now),
        )
        self.connection.commit()
        self.audit(None, "account.created", "account", account["id"], {"username": username, "is_admin": is_admin})
        return account

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM accounts WHERE username = ? AND enabled = 1", (username,)).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return None
        return _public_account(row)

    def set_account(self, actor_id: str, account_id: str, *, enabled: bool | None = None, password: str | None = None) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise KeyError("account not found")
        fields: list[str] = []
        values: list[Any] = []
        if enabled is not None:
            fields.append("enabled = ?")
            values.append(int(enabled))
        if password is not None:
            fields.append("password_hash = ?")
            values.append(hash_password(password))
        if not fields:
            return _public_account(row)
        fields.append("updated_at = ?")
        values.extend((_now(), account_id))
        self.connection.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id = ?", values)
        self.connection.commit()
        self.audit(actor_id, "account.updated", "account", account_id, {"enabled": enabled, "password_reset": password is not None})
        return _public_account(self.connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone())

    def create_topic(self, actor_id: str, name: str, keywords: list[str], enabled: bool = True) -> dict[str, Any]:
        now = _now()
        topic_id = _id()
        self.connection.execute(
            "INSERT INTO topics(id, name, keywords, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (topic_id, name, json.dumps(keywords), int(enabled), now, now),
        )
        self.connection.commit()
        self.audit(actor_id, "topic.created", "topic", topic_id, {"name": name})
        return {"id": topic_id, "name": name, "keywords": keywords, "enabled": enabled, "created_at": now, "updated_at": now}

    def list_topics(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM topics" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY name"
        return [_topic(row) for row in self.connection.execute(query)]

    def set_topic(self, actor_id: str, topic_id: str, *, enabled: bool | None = None, keywords: list[str] | None = None) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
        if row is None:
            raise KeyError("topic not found")
        fields: list[str] = []
        values: list[Any] = []
        if enabled is not None:
            fields.append("enabled = ?")
            values.append(int(enabled))
        if keywords is not None:
            fields.append("keywords = ?")
            values.append(json.dumps(keywords))
        if not fields:
            return _topic(row)
        fields.append("updated_at = ?")
        values.extend((_now(), topic_id))
        self.connection.execute(f"UPDATE topics SET {', '.join(fields)} WHERE id = ?", values)
        self.connection.commit()
        self.audit(actor_id, "topic.updated", "topic", topic_id, {"enabled": enabled, "keywords_changed": keywords is not None})
        return _topic(self.connection.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone())

    def add_recommendation(self, actor_id: str, topic_id: str, recommendation_date: str, score: float, rationale: str) -> dict[str, Any]:
        if self.connection.execute("SELECT 1 FROM topics WHERE id = ?", (topic_id,)).fetchone() is None:
            raise KeyError("topic not found")
        recommendation = (_id(), topic_id, recommendation_date, score, rationale, _now())
        self.connection.execute("INSERT INTO recommendations VALUES (?, ?, ?, ?, ?, ?)", recommendation)
        self.connection.commit()
        self.audit(actor_id, "recommendation.created", "topic", topic_id, {"date": recommendation_date, "score": score})
        return {"id": recommendation[0], "topic_id": topic_id, "recommendation_date": recommendation_date, "score": score, "rationale": rationale, "created_at": recommendation[5]}

    def list_recommendations(self, recommendation_date: str | None = None) -> list[dict[str, Any]]:
        if recommendation_date is None:
            rows = self.connection.execute("SELECT * FROM recommendations ORDER BY recommendation_date DESC, score DESC")
        else:
            rows = self.connection.execute("SELECT * FROM recommendations WHERE recommendation_date = ? ORDER BY score DESC", (recommendation_date,))
        return [{"id": row["id"], "topic_id": row["topic_id"], "recommendation_date": row["recommendation_date"], "score": row["score"], "rationale": row["rationale"], "created_at": row["created_at"]} for row in rows]

    def create_task(self, actor_id: str, title: str, topic_id: str | None = None, risk_level: str = "LOW") -> dict[str, Any]:
        if risk_level not in {"LOW", "HIGH", "BLOCKED"}:
            raise ValueError("invalid risk level")
        if topic_id is not None and self.connection.execute("SELECT 1 FROM topics WHERE id = ?", (topic_id,)).fetchone() is None:
            raise KeyError("topic not found")
        now = _now()
        task_id = _id()
        self.connection.execute(
            "INSERT INTO tasks(id, topic_id, title, status, risk_level, current_script_version, current_media_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, topic_id, title, "DISCOVERED", risk_level, None, None, now, now),
        )
        self.connection.commit()
        self.audit(actor_id, "task.created", "task", task_id, {"title": title})
        return self.get_task(task_id)

    def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is None:
            rows = self.connection.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        else:
            rows = self.connection.execute("SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC", (status,))
        return [self._task(row) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("task not found")
        return self._task(row)

    def transition_task(self, actor_id: str, task_id: str, target: str, reason: str = "") -> None:
        if self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_tasks'").fetchone():
            if self.connection.execute("SELECT 1 FROM workflow_tasks WHERE task_id=?", (task_id,)).fetchone():
                raise ValueError("managed task transitions must execute through the workflow engine")
        row = self.connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("task not found")
        validate_transition(row["status"], target)
        self.connection.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (target, _now(), task_id))
        self.connection.commit()
        self.audit(actor_id, "task.transitioned", "task", task_id, {"from": row["status"], "to": target, "reason": reason})

    def add_script_version(self, actor_id: str, task_id: str, content: str) -> dict[str, Any]:
        self.get_task(task_id)
        version = self._next_version("script_versions", task_id)
        return self._add_version("script_versions", actor_id, task_id, version, content=content)

    def add_media_version(self, actor_id: str, task_id: str, uri: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        self.get_task(task_id)
        version = self._next_version("media_versions", task_id)
        return self._add_version("media_versions", actor_id, task_id, version, uri=uri, metadata=metadata or {})

    def add_approval(self, actor_id: str, task_id: str, kind: str, decision: str, comment: str = "", expected_version: int | None = None) -> dict[str, Any]:
        if decision not in {"APPROVED", "REJECTED"}:
            raise ValueError("invalid approval decision")
        if kind not in {"SCRIPT", "VIDEO"}:
            raise ValueError("invalid approval kind")
        task = self.get_task(task_id)
        actual_version = task["current_script_version"] if kind == "SCRIPT" else task["current_media_version"]
        if expected_version is not None and expected_version != actual_version:
            raise ValueError("approval version is no longer current")
        if self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_tasks'").fetchone():
            managed = self.connection.execute("SELECT payload FROM workflow_tasks WHERE task_id=?", (task_id,)).fetchone()
            if managed:
                workflow = json.loads(managed[0])
                expected_status = "SCRIPT_PENDING_APPROVAL" if kind == "SCRIPT" else "VIDEO_PENDING_APPROVAL"
                if workflow["status"] != expected_status or task["risk_level"] == "BLOCKED":
                    raise ValueError("task is not accepting this approval")
                if task["current_script_version"] != workflow["script_version"] or (kind == "VIDEO" and actual_version != workflow["media_version"]):
                    raise ValueError("workflow content version changed")
        approval_id = _id()
        now = _now()
        self.connection.execute("INSERT INTO approvals(id, task_id, kind, decision, comment, decided_by, created_at, script_version, media_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (approval_id, task_id, kind, decision, comment, actor_id, now, task["current_script_version"], task["current_media_version"]))
        self.connection.commit()
        self.audit(actor_id, "approval.created", "task", task_id, {"kind": kind, "decision": decision})
        return {"id": approval_id, "task_id": task_id, "kind": kind, "decision": decision, "comment": comment, "decided_by": actor_id, "created_at": now, "script_version": task["current_script_version"], "media_version": task["current_media_version"]}

    def add_asset(self, actor_id: str, task_id: str, kind: str, uri: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        self.get_task(task_id)
        asset = (_id(), task_id, kind, uri, json.dumps(metadata or {}), _now())
        self.connection.execute("INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?)", asset)
        self.connection.commit()
        self.audit(actor_id, "asset.created", "task", task_id, {"kind": kind, "uri": uri})
        return {"id": asset[0], "task_id": task_id, "kind": kind, "uri": uri, "metadata": metadata or {}, "created_at": asset[5]}

    def add_publication_receipt(self, actor_id: str, task_id: str, platform: str, external_id: str, status: str, response: dict[str, Any] | None = None) -> dict[str, Any]:
        self.get_task(task_id)
        receipt = (_id(), task_id, platform, external_id, status, json.dumps(response or {}), _now())
        self.connection.execute("INSERT INTO publication_receipts VALUES (?, ?, ?, ?, ?, ?, ?)", receipt)
        self.connection.commit()
        self.audit(actor_id, "publication.receipt.created", "task", task_id, {"platform": platform, "status": status})
        return {"id": receipt[0], "task_id": task_id, "platform": platform, "external_id": external_id, "status": status, "response": response or {}, "created_at": receipt[6]}

    def save_metric_snapshot(self, snapshot_date: str, metrics: dict[str, Any]) -> dict[str, Any]:
        snapshot = (_id(), snapshot_date, json.dumps(metrics), _now())
        self.connection.execute("INSERT OR REPLACE INTO metric_snapshots(id, snapshot_date, metrics, created_at) VALUES (?, ?, ?, ?)", snapshot)
        self.connection.commit()
        return {"id": snapshot[0], "snapshot_date": snapshot_date, "metrics": metrics, "created_at": snapshot[3]}

    def audit(self, actor_id: str | None, action: str, resource_type: str, resource_id: str, details: dict[str, Any]) -> None:
        self.connection.execute("INSERT INTO audit_logs VALUES (?, ?, ?, ?, ?, ?, ?)", (_id(), actor_id, action, resource_type, resource_id, json.dumps(details), _now()))
        self.connection.commit()

    def audit_for(self, resource_type: str, resource_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM audit_logs WHERE resource_type = ? AND resource_id = ? ORDER BY created_at", (resource_type, resource_id))
        return [{"id": row["id"], "actor_id": row["actor_id"], "action": row["action"], "details": json.loads(row["details"]), "created_at": row["created_at"]} for row in rows]

    def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=7)
        deleted = 0
        for table in ("recommendations", "script_versions", "media_versions", "approvals", "assets", "publication_receipts", "metric_snapshots", "audit_logs"):
            deleted += self.connection.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff.isoformat(),)).rowcount
        self.connection.execute(
            "UPDATE tasks SET current_script_version = NULL WHERE current_script_version IS NOT NULL AND NOT EXISTS (SELECT 1 FROM script_versions WHERE script_versions.task_id = tasks.id AND script_versions.version = tasks.current_script_version)"
        )
        self.connection.execute(
            "UPDATE tasks SET current_media_version = NULL WHERE current_media_version IS NOT NULL AND NOT EXISTS (SELECT 1 FROM media_versions WHERE media_versions.task_id = tasks.id AND media_versions.version = tasks.current_media_version)"
        )
        self.connection.commit()
        return deleted

    def _next_version(self, table: str, task_id: str) -> int:
        row = self.connection.execute(f"SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM {table} WHERE task_id = ?", (task_id,)).fetchone()
        return int(row["next_version"])

    def _add_version(self, table: str, actor_id: str, task_id: str, version: int, **values: Any) -> dict[str, Any]:
        created_at = _now()
        if table == "script_versions":
            item = (_id(), task_id, version, values["content"], actor_id, created_at)
            self.connection.execute("INSERT INTO script_versions VALUES (?, ?, ?, ?, ?, ?)", item)
            self.connection.execute("UPDATE tasks SET current_script_version = ?, updated_at = ? WHERE id = ?", (version, created_at, task_id))
            result = {"id": item[0], "task_id": task_id, "version": version, "content": values["content"], "created_by": actor_id, "created_at": created_at}
        else:
            metadata = values["metadata"]
            item = (_id(), task_id, version, values["uri"], json.dumps(metadata), actor_id, created_at)
            self.connection.execute("INSERT INTO media_versions VALUES (?, ?, ?, ?, ?, ?, ?)", item)
            self.connection.execute("UPDATE tasks SET current_media_version = ?, updated_at = ? WHERE id = ?", (version, created_at, task_id))
            result = {"id": item[0], "task_id": task_id, "version": version, "uri": values["uri"], "metadata": metadata, "created_by": actor_id, "created_at": created_at}
        self.connection.commit()
        self.audit(actor_id, f"{table[:-1]}.created", "task", task_id, {"version": version})
        return result

    def _task(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["topic"] = None
        if row["topic_id"]:
            topic = self.connection.execute("SELECT name FROM topics WHERE id = ?", (row["topic_id"],)).fetchone()
            result["topic"] = topic["name"] if topic else None
        result["audit"] = self.audit_for("task", row["id"])
        return result


def _id() -> str:
    return secrets.token_urlsafe(12)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_account(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "username": row["username"], "enabled": bool(row["enabled"]), "is_admin": bool(row["is_admin"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _topic(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["keywords"] = json.loads(result["keywords"])
    result["enabled"] = bool(result["enabled"])
    return result
