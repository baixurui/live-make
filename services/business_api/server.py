from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .app import BusinessApi, json_response
from .db import Database


class RequestHandler(BaseHTTPRequestHandler):
    api: BusinessApi

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def _handle(self, method: str) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body: dict[str, Any] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write(400, {"error": "request body must be valid JSON"})
            return
        response = self.api.request(method, self.path, dict(self.headers), body)
        status, payload = json_response(response)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _write(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    database = Database(os.environ.get("BUSINESS_API_DATABASE", "business-api.sqlite3"))
    _bootstrap_admin(database)
    api = BusinessApi(database, publishing_token=os.environ.get("BUSINESS_API_PUBLISHING_TOKEN"))
    RequestHandler.api = api
    server = ThreadingHTTPServer((os.environ.get("BUSINESS_API_HOST", "127.0.0.1"), int(os.environ.get("BUSINESS_API_PORT", "8080"))), RequestHandler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        database.close()


def _bootstrap_admin(database: Database) -> None:
    username = os.environ.get("BUSINESS_API_ADMIN_USERNAME")
    password = os.environ.get("BUSINESS_API_ADMIN_PASSWORD")
    existing = database.connection.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()["count"]
    if existing == 0 and username and password:
        database.create_account(username, password, is_admin=True)


if __name__ == "__main__":
    main()
