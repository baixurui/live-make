from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from .db import Database


@dataclass(frozen=True)
class Response:
    status: int
    body: dict[str, Any] | list[Any] | None = None


class BusinessApi:
    def __init__(self, database: Database | None = None, session_ttl: timedelta = timedelta(hours=8)) -> None:
        self.database = database or Database()
        self.session_ttl = session_ttl
        self.sessions: dict[str, tuple[str, datetime]] = {}

    def request(self, method: str, path: str, headers: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> Response:
        headers = {key.lower(): value for key, value in (headers or {}).items()}
        body = body or {}
        parsed = urlparse(path)
        route = parsed.path.rstrip("/") or "/"
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        try:
            if method == "POST" and route == "/api/v1/auth/login":
                return self._login(body)
            if method == "POST" and route.startswith("/api/v1/internal/tasks/") and route.endswith("/transitions"):
                if headers.get("x-internal-service") != "workflow":
                    raise PermissionError("workflow service identity required")
                task_id = route.split("/")[5]
                self.database.transition_task(headers.get("x-service-id", "workflow"), task_id, str(body["target_status"]), str(body.get("reason", "")))
                return Response(204)
            if method == "POST" and route.startswith("/api/v1/internal/tasks/") and route.endswith("/script-versions"):
                self._require_internal_service(headers, {"content-intelligence"})
                task_id = route.split("/")[5]
                return Response(201, self.database.add_script_version(headers.get("x-service-id", "content-intelligence"), task_id, str(body["content"])))
            if method == "POST" and route.startswith("/api/v1/internal/tasks/") and route.endswith("/media-versions"):
                self._require_internal_service(headers, {"media-production"})
                task_id = route.split("/")[5]
                return Response(201, self.database.add_media_version(headers.get("x-service-id", "media-production"), task_id, str(body["uri"]), body.get("metadata")))
            if method == "POST" and route.startswith("/api/v1/internal/tasks/") and route.endswith("/assets"):
                self._require_internal_service(headers, {"media-production"})
                task_id = route.split("/")[5]
                return Response(201, self.database.add_asset(headers.get("x-service-id", "media-production"), task_id, str(body["kind"]), str(body["uri"]), body.get("metadata")))
            if method == "POST" and route.startswith("/api/v1/internal/tasks/") and route.endswith("/publication-receipts"):
                self._require_internal_service(headers, {"publishing-insights"})
                task_id = route.split("/")[5]
                return Response(201, self.database.add_publication_receipt(headers.get("x-service-id", "publishing-insights"), task_id, str(body["platform"]), str(body["external_id"]), str(body["status"]), body.get("response")))
            actor = self._actor(headers)
            if method == "GET" and route == "/api/v1/topics":
                return Response(200, self.database.list_topics(enabled_only=True))
            if method == "POST" and route == "/api/v1/topics":
                self._require_admin(actor)
                return Response(201, self.database.create_topic(actor["id"], str(body["name"]), list(body.get("keywords", [])), bool(body.get("enabled", True))))
            if method == "PATCH" and route.startswith("/api/v1/topics/"):
                self._require_admin(actor)
                topic_id = route.split("/")[4]
                return Response(200, self.database.set_topic(actor["id"], topic_id, enabled=body.get("enabled"), keywords=body.get("keywords")))
            if method == "GET" and route == "/api/v1/recommendations":
                return Response(200, self.database.list_recommendations(query.get("date")))
            if method == "GET" and route == "/api/v1/tasks":
                return Response(200, self.database.list_tasks(query.get("status")))
            if method == "POST" and route == "/api/v1/tasks":
                return Response(201, self.database.create_task(actor["id"], str(body["title"]), body.get("topic_id"), str(body.get("risk_level", "LOW"))))
            if method == "GET" and route.startswith("/api/v1/tasks/") and route.endswith("/audit"):
                task_id = route.split("/")[4]
                self.database.get_task(task_id)
                return Response(200, self.database.audit_for("task", task_id))
            if method == "GET" and route.startswith("/api/v1/tasks/"):
                return Response(200, self.database.get_task(route.split("/")[4]))
            if method == "POST" and route.startswith("/api/v1/tasks/") and route.endswith("/approvals"):
                task_id = route.split("/")[4]
                return Response(201, self.database.add_approval(actor["id"], task_id, str(body["kind"]), str(body["decision"]), str(body.get("comment", ""))))
            if method == "GET" and route == "/api/v1/metrics":
                return Response(200, self._metrics())
            if method == "POST" and route == "/api/v1/admin/accounts":
                self._require_admin(actor)
                return Response(201, self.database.create_account(str(body["username"]), str(body["password"]), bool(body.get("is_admin", False))))
            if method == "PATCH" and route.startswith("/api/v1/admin/accounts/"):
                self._require_admin(actor)
                account_id = route.split("/")[5]
                return Response(200, self.database.set_account(actor["id"], account_id, enabled=body.get("enabled"), password=body.get("password")))
            return Response(404, {"error": "not found"})
        except (KeyError, ValueError) as error:
            return Response(400, {"error": str(error)})
        except sqlite3.IntegrityError as error:
            return Response(409, {"error": str(error)})
        except PermissionError as error:
            return Response(403, {"error": str(error)})

    def _login(self, body: dict[str, Any]) -> Response:
        account = self.database.authenticate(str(body.get("username", "")), str(body.get("password", "")))
        if account is None:
            return Response(401, {"error": "invalid credentials"})
        token = secrets.token_urlsafe(32)
        self.sessions[token] = (account["id"], datetime.now(timezone.utc) + self.session_ttl)
        return Response(200, {"access_token": token, "token_type": "Bearer", "expires_in": int(self.session_ttl.total_seconds()), "account": account})

    def _actor(self, headers: dict[str, str]) -> dict[str, Any]:
        authorization = headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise PermissionError("authentication required")
        session = self.sessions.get(authorization[7:])
        if session is None or session[1] <= datetime.now(timezone.utc):
            raise PermissionError("session expired")
        row = self.database.connection.execute("SELECT * FROM accounts WHERE id = ? AND enabled = 1", (session[0],)).fetchone()
        if row is None:
            raise PermissionError("account disabled")
        return {"id": row["id"], "username": row["username"], "is_admin": bool(row["is_admin"])}

    @staticmethod
    def _require_admin(actor: dict[str, Any]) -> None:
        if not actor["is_admin"]:
            raise PermissionError("administrator permission required")

    @staticmethod
    def _require_internal_service(headers: dict[str, str], allowed: set[str]) -> None:
        if headers.get("x-internal-service") not in allowed:
            raise PermissionError("authorized internal service identity required")

    def _metrics(self) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        total = self.database.connection.execute("SELECT COUNT(*) AS count FROM tasks WHERE created_at >= ?", (since,)).fetchone()["count"]
        published = self.database.connection.execute("SELECT COUNT(*) AS count FROM tasks WHERE status = 'PUBLISHED' AND updated_at >= ?", (since,)).fetchone()["count"]
        failed = self.database.connection.execute("SELECT COUNT(*) AS count FROM tasks WHERE status = 'FAILED' AND updated_at >= ?", (since,)).fetchone()["count"]
        return {"window_days": 7, "tasks_created": total, "tasks_published": published, "tasks_failed": failed}


def json_response(response: Response) -> tuple[int, bytes]:
    if response.body is None:
        return response.status, b""
    return response.status, json.dumps(response.body, ensure_ascii=False).encode("utf-8")
