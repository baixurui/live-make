from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from services.business_api.state_machine import validate_transition


BEIJING = timezone(timedelta(hours=8))
OUTPUT_SPEC = {"width": 720, "height": 1280, "fps": 25, "video_bitrate_bps": 2500000,
               "container": "mp4", "video_codec": "h264", "audio_codec": "aac",
               "duration_min_seconds": 7, "duration_max_seconds": 15,
               "subtitle_language": "zh-CN", "subtitles_burned_in": True}
SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_tasks (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_inbox (
 event_id TEXT PRIMARY KEY, event_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS workflow_outbox (
 event_id TEXT PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
 delivered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS workflow_scans (
 topic_id TEXT NOT NULL, scan_date TEXT NOT NULL, status TEXT NOT NULL,
 automatic_retry INTEGER NOT NULL DEFAULT 0, manual_retry INTEGER NOT NULL DEFAULT 0,
 request_key TEXT NOT NULL, PRIMARY KEY(topic_id, scan_date)
);
"""


class Workflow:
    def __init__(self, database, clock=None):
        self.database = database
        self.connection = database.connection
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def now(self):
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("clock must be timezone-aware")
        return value.astimezone(BEIJING)

    @contextmanager
    def atomic(self):
        if self.connection.in_transaction:
            raise RuntimeError("workflow requires an idle, dedicated database connection")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def inspect(self, task_id):
        row = self.connection.execute("SELECT payload FROM workflow_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("task not registered with workflow")
        return json.loads(row[0])

    def _save(self, task):
        self.connection.execute("UPDATE workflow_tasks SET payload=? WHERE task_id=?", (json.dumps(task), task["task_id"]))

    def _move(self, task, target, reason=""):
        current = self.database.get_task(task["task_id"])["status"]
        validate_transition(current, target)
        now = self.now().isoformat()
        self.connection.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=? AND status=?", (target, now, task["task_id"], current))
        self.connection.execute("INSERT INTO audit_logs VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (str(uuid4()), "workflow", "task.transitioned", "task", task["task_id"],
                                 json.dumps({"from": current, "to": target, "reason": reason}), now))
        task["status"] = target
        task["reason"] = reason
        self._save(task)

    def _emit(self, event_type, data, key):
        if event_type in {"task.script_requested.v1", "task.media_requested.v1", "task.publish_requested.v1"}:
            task = self.inspect(data["task_id"])
            task["active_request_key"] = key
            self._save(task)
        event = {"event_id": str(uuid4()), "event_type": event_type, "occurred_at": self.now().isoformat(),
                 "correlation_id": data.get("task_id", data.get("topic_id")), "idempotency_key": key, "data": data}
        self.connection.execute("INSERT OR IGNORE INTO workflow_outbox(event_id,event_key,payload) VALUES (?,?,?)",
                                (event["event_id"], key, json.dumps(event)))

    def register(self, task_id, account_id, script_version=1, media_version=1, voice_version="voice-v1"):
        with self.atomic():
            source = self.database.get_task(task_id)
            if source["status"] != "DISCOVERED" or source["risk_level"] == "BLOCKED":
                raise ValueError("task must be unblocked and DISCOVERED")
            if not account_id or script_version < 1 or media_version < 1:
                raise ValueError("account and positive versions required")
            task = {"task_id": task_id, "account_id": account_id,
                    "script_version": script_version, "media_version": media_version,
                    "voice_version": voice_version, "risk_level": source["risk_level"],
                    "status": source["status"], "date": self.now().date().isoformat(),
                    "approvals": {"SCRIPT": {}, "VIDEO": {}}, "qc_passed": False,
                    "attempts": {}, "retry_at": None, "scheduled_at": None, "generation": 1}
            self.connection.execute("INSERT INTO workflow_tasks VALUES (?,?)", (task_id, json.dumps(task)))
            self._move(task, "TOPIC_SELECTED")
            self._move(task, "SCRIPT_GENERATING")
            self._script(task)

    def _script(self, task):
        source = self.database.get_task(task["task_id"])
        topics = {topic["id"]: topic for topic in self.database.list_topics()}
        self._emit("task.script_requested.v1", {"task_id": task["task_id"], "topic_id": source["topic_id"],
                   "keywords": topics.get(source["topic_id"], {}).get("keywords", []), "script_version": task["script_version"]},
                   f"{task['task_id']}:script:{task['script_version']}:attempt:{task['attempts'].get('script', 0)}")
        task.update(self.inspect(task["task_id"]))

    def _current(self, task):
        source = self.database.get_task(task["task_id"])
        if source["current_script_version"] != task["script_version"]:
            raise ValueError("script changed or not persisted")
        if task["qc_passed"] and source["current_media_version"] != task["media_version"]:
            raise ValueError("media changed; restart with the new version")
        if source["risk_level"] != task["risk_level"]:
            raise ValueError("risk changed; review again")

    def _approved(self, task, kind):
        decisions = list(task["approvals"][kind].values())
        return (task["risk_level"] != "BLOCKED" and not any(item["decision"] == "REJECTED" for item in decisions)
                and sum(item["decision"] == "APPROVED" for item in decisions) >= (2 if task["risk_level"] == "HIGH" else 1))

    def review(self, task_id):
        with self.atomic():
            task = self.inspect(task_id)
            self._current(task)
            kind = {"SCRIPT_PENDING_APPROVAL": "SCRIPT", "VIDEO_PENDING_APPROVAL": "VIDEO"}.get(task["status"])
            if kind is None:
                raise ValueError("task is not awaiting approval")
            if self.now() >= datetime.fromisoformat(task["date"]).replace(hour=11, minute=30, tzinfo=BEIJING):
                self._move(task, "PAUSED", "approval cutoff")
                return
            rows = self.connection.execute("SELECT approvals.* FROM approvals JOIN accounts ON accounts.id=decided_by WHERE task_id=? AND kind=? AND accounts.enabled=1 ORDER BY created_at,id", (task_id, kind))
            votes = {}
            for row in rows:
                if row["script_version"] != task["script_version"] or (kind == "VIDEO" and row["media_version"] != task["media_version"]):
                    continue
                previous = votes.get(row["decided_by"])
                if previous is None or row["decision"] == "REJECTED":
                    votes[row["decided_by"]] = {"id": row["id"], "decision": row["decision"]}
            task["approvals"][kind] = votes
            self._save(task)
            if any(vote["decision"] == "REJECTED" for vote in votes.values()):
                self._move(task, "SCRIPT_REJECTED" if kind == "SCRIPT" else "VIDEO_REJECTED", "human rejection")
            elif self._approved(task, kind):
                self._move(task, "SCRIPT_APPROVED" if kind == "SCRIPT" else "VIDEO_APPROVED")
                data = {"task_id": task_id, "risk_level": task["risk_level"], "approval_ids": [vote["id"] for vote in votes.values()],
                        "script_version" if kind == "SCRIPT" else "media_version": task["script_version" if kind == "SCRIPT" else "media_version"]}
                self._emit("task.script_approved.v1" if kind == "SCRIPT" else "task.video_approved.v1", data,
                           f"{task_id}:{kind}:approved:{task['generation']}")
                if kind == "SCRIPT":
                    self._move(task, "MEDIA_GENERATING")
                    self._media(task)
                else:
                    self._schedule(task, self.now().date().isoformat())

    def _media(self, task):
        script = self.connection.execute("SELECT content FROM script_versions WHERE task_id=? AND version=?", (task["task_id"], task["script_version"])).fetchone()
        if script is None:
            raise ValueError("approved script is unavailable")
        data = {key: task[key] for key in ("task_id", "script_version", "media_version", "voice_version")}
        data.update(script=script[0], output_spec=OUTPUT_SPEC, resume_from=task.get("failed_step"))
        self._emit("task.media_requested.v1", data, f"{task['task_id']}:media:{task['media_version']}:attempt:{task['attempts'].get('media', 0)}")
        task.update(self.inspect(task["task_id"]))

    def _schedule(self, task, date):
        if not self._approved(task, "SCRIPT") or not self._approved(task, "VIDEO") or not task["qc_passed"]:
            raise ValueError("both approval gates and media QC are required")
        parsed = datetime.strptime(date, "%Y-%m-%d")
        scheduled = parsed.replace(hour=12, tzinfo=BEIJING)
        if self.now() >= scheduled - timedelta(minutes=30):
            if task["status"] != "PAUSED":
                self._move(task, "PAUSED", "11:30 approval cutoff missed")
            return False
        for row in self.connection.execute("SELECT payload FROM workflow_tasks WHERE task_id != ?", (task["task_id"],)):
            other = json.loads(row[0])
            if other["account_id"] == task["account_id"] and other.get("scheduled_at") == scheduled.isoformat() and other["status"] != "CANCELLED":
                if task["status"] != "PAUSED":
                    self._move(task, "PAUSED", "account daily slot occupied")
                return False
        task["scheduled_at"] = scheduled.isoformat()
        task["date"] = date
        self._move(task, "SCHEDULED")
        self._emit("task.scheduled.v1", {"task_id": task["task_id"], "scheduled_at": task["scheduled_at"], "media_version": task["media_version"]},
                   f"{task['task_id']}:scheduled:{date}:{task['generation']}")
        return True

    def pause(self, task_id):
        with self.atomic():
            task = self.inspect(task_id)
            if task["status"] == "PUBLISHING":
                raise ValueError("in-flight publication must be reconciled before pausing")
            if task["status"] != "PAUSED":
                task["retry_at"] = None
                self._move(task, "PAUSED", "manual pause")

    def reschedule(self, task_id, date):
        with self.atomic():
            task = self.inspect(task_id)
            self._current(task)
            if task["status"] != "PAUSED":
                raise ValueError("only PAUSED tasks can be rescheduled")
            if task.get("receipt_id"):
                raise ValueError("publication was attempted; reconcile receipt before rescheduling")
            return self._schedule(task, date)

    def resume(self, task_id):
        with self.atomic():
            task = self.inspect(task_id)
            if task["status"] != "PAUSED" or task.get("receipt_id"):
                raise ValueError("only unpublished PAUSED tasks can resume")
            source = self.database.get_task(task_id)
            if source["current_script_version"] not in (None, task["script_version"]):
                raise ValueError("changed content requires restart")
            if source["risk_level"] == "BLOCKED" or source["risk_level"] != task["risk_level"]:
                raise ValueError("changed risk requires rebuilding and review")
            task["date"] = self.now().date().isoformat()
            if self.now().hour > 11 or (self.now().hour == 11 and self.now().minute >= 30):
                raise ValueError("resume before 11:30")
            if task["risk_level"] == "BLOCKED":
                raise ValueError("blocked content requires rebuilding")
            if source["current_script_version"] is None or (not task.get("script_reviewed") and not self._approved(task, "SCRIPT")):
                task["attempts"]["script"] = task["attempts"].get("script", 0) + 1
                self._move(task, "SCRIPT_GENERATING", "resume failed script operation")
                self._script(task)
            elif task["qc_passed"]:
                self._current(task)
                self._move(task, "VIDEO_PENDING_APPROVAL", "resume existing media")
            elif self._approved(task, "SCRIPT"):
                self._current(task)
                task["attempts"]["media"] = task["attempts"].get("media", 0) + 1
                self._move(task, "MEDIA_GENERATING", "resume failed media node")
                self._media(task)
            else:
                self._current(task)
                self._move(task, "SCRIPT_PENDING_APPROVAL", "resume existing script review")

    def cancel(self, task_id):
        with self.atomic():
            task = self.inspect(task_id)
            if task["status"] == "CANCELLED":
                return
            task["retry_at"] = None
            self._move(task, "CANCELLED", "user cancelled")

    def restart(self, task_id, script_version=None, media_version=None):
        with self.atomic():
            task = self.inspect(task_id)
            if task["status"] not in {"PAUSED", "SCRIPT_REJECTED", "VIDEO_REJECTED"}:
                raise ValueError("task must be paused or rejected before rebuilding")
            source = self.database.get_task(task_id)
            script_changed = script_version is not None and script_version > task["script_version"]
            if task.get("receipt_id"):
                raise ValueError("publication must be reconciled before rebuilding")
            if not script_changed and (media_version is None or media_version <= task["media_version"]):
                raise ValueError("a new content version is required")
            task["generation"] += 1
            task["scheduled_at"] = None
            task["retry_at"] = None
            task["attempts"] = {}
            task.pop("failed_stage", None)
            task.pop("failed_step", None)
            task["date"] = self.now().date().isoformat()
            if not script_changed:
                self._current(task)
            task["qc_passed"] = False
            task["approvals"]["VIDEO"] = {}
            if script_changed:
                task["script_reviewed"] = False
                task["script_version"] = script_version
                task["media_version"] = max(task["media_version"] + 1, media_version or 1)
                task["approvals"]["SCRIPT"] = {}
                task["risk_level"] = source["risk_level"]
                self._move(task, "SCRIPT_GENERATING", "new script invalidates approvals")
                self._script(task)
            else:
                if not self._approved(task, "SCRIPT"):
                    raise ValueError("script gate required")
                task["media_version"] = media_version
                self._move(task, "MEDIA_GENERATING", "new media invalidates video approval")
                self._media(task)

    def receive(self, event):
        required = {"event_id", "event_type", "occurred_at", "correlation_id", "idempotency_key", "data"}
        if not required <= event.keys() or not event["event_id"] or not event["idempotency_key"]:
            raise ValueError("invalid event envelope")
        with self.atomic():
            event_key = event["event_type"] + ":" + event["idempotency_key"]
            if self.connection.execute("SELECT 1 FROM workflow_inbox WHERE event_id=? OR event_key=?", (event["event_id"], event_key)).fetchone():
                return False
            data = event["data"]
            task = self.inspect(data["task_id"])
            if event["correlation_id"] != task["task_id"]:
                raise ValueError("event correlation_id must match task")
            if task["status"] not in {"CANCELLED", "PUBLISHED", "PAUSED"}:
                self._result(task, event["event_type"], data)
            self.connection.execute("INSERT INTO workflow_inbox VALUES (?,?)", (event["event_id"], event_key))
            return True

    def _result(self, task, event_type, data):
        if event_type == "task.script_reviewed.v1":
            if data["script_version"] != task["script_version"]:
                return
            if task["status"] != "SCRIPT_GENERATING":
                raise ValueError("unexpected script result")
            self._current(task)
            if data["risk_level"] not in {"LOW", "HIGH", "BLOCKED"}:
                raise ValueError("invalid risk")
            task["risk_level"] = data["risk_level"]
            task["script_reviewed"] = True
            self.connection.execute("UPDATE tasks SET risk_level=? WHERE id=?", (task["risk_level"], task["task_id"]))
            self._move(task, "SCRIPT_AUTO_REVIEWING")
            self._move(task, "SCRIPT_REJECTED" if task["risk_level"] == "BLOCKED" else "SCRIPT_PENDING_APPROVAL")
        elif event_type == "task.media_qc_completed.v1":
            if data["media_version"] != task["media_version"]:
                return
            if task["status"] != "MEDIA_GENERATING":
                raise ValueError("unexpected media result")
            self._current(task)
            if self.database.get_task(task["task_id"])["current_media_version"] != task["media_version"]:
                raise ValueError("media result must be persisted before QC notification")
            if data["qc_decision"] not in {"PASS", "FAIL"}:
                raise ValueError("unknown QC decision")
            task["qc_passed"] = data["qc_decision"] == "PASS" and not any(item.get("hard_stop", True) for item in data["failures"])
            task["asset_ids"] = data["asset_ids"]
            self._move(task, "MEDIA_QC_RUNNING")
            self._move(task, "VIDEO_PENDING_APPROVAL" if task["qc_passed"] else "VIDEO_REJECTED")
        elif event_type == "task.published.v1":
            if task["status"] != "PUBLISHING" or data["receipt_id"] != task["receipt_id"]:
                raise ValueError("unexpected publication receipt")
            self._move(task, "PUBLISHED")
        elif event_type == "task.failed.v1":
            stage = {"SCRIPT_GENERATING": "script", "MEDIA_GENERATING": "media", "PUBLISHING": "publish"}.get(task["status"])
            if stage is None:
                raise ValueError("no active operation to fail")
            expected_key = task.get("active_request_key")
            if data.get("request_key") != expected_key:
                raise ValueError("failure must identify the active request_key")
            attempt = task["attempts"].get(stage, 0)
            if data["attempt"] != attempt:
                return
            task["failed_step"] = data["failed_step"]
            task["failed_stage"] = stage
            task["retry_at"] = None
            limit = 1 if stage == "publish" else 3
            if data["retryable"] is True and attempt < limit:
                task["attempts"][stage] = attempt + 1
                task["retry_at"] = (self.now() + timedelta(minutes=(1, 5, 15)[attempt])).isoformat()
                self._move(task, "FAILED", data["error_code"])
            else:
                self._move(task, "PAUSED", data["error_code"])
        else:
            raise ValueError("unsupported workflow event")

    def tick(self):
        awaiting = [json.loads(row[0]) for row in self.connection.execute("SELECT payload FROM workflow_tasks")]
        for task in awaiting:
            if task["status"] in {"SCRIPT_PENDING_APPROVAL", "VIDEO_PENDING_APPROVAL"}:
                try:
                    self.review(task["task_id"])
                except ValueError:
                    self.pause(task["task_id"])
        with self.atomic():
            now = self.now()
            self._scans(now)
            tasks = [json.loads(row[0]) for row in self.connection.execute("SELECT payload FROM workflow_tasks")]
            for task in tasks:
                if task["status"] in {"CANCELLED", "PUBLISHED", "PAUSED"}:
                    continue
                cutoff = datetime.fromisoformat(task["date"]).replace(hour=11, minute=30, tzinfo=BEIJING)
                if now >= cutoff and task["status"] not in {"SCHEDULED", "PUBLISHING"} and task.get("failed_stage") != "publish":
                    task["retry_at"] = None
                    self._move(task, "PAUSED", "approval cutoff")
                    continue
                if task["status"] == "FAILED" and task["retry_at"] and now >= datetime.fromisoformat(task["retry_at"]):
                    stage = task["failed_stage"]
                    if stage != "script":
                        try:
                            self._current(task)
                        except ValueError:
                            self._move(task, "PAUSED", "content changed before retry")
                            continue
                    elif self.database.get_task(task["task_id"])["current_script_version"] not in (None, task["script_version"]):
                        self._move(task, "PAUSED", "script changed before retry")
                        continue
                    if stage == "publish" and now.date().isoformat() != task["date"]:
                        self._move(task, "PAUSED", "publication retry day missed")
                        continue
                    task["retry_at"] = None
                    self._move(task, {"script": "SCRIPT_GENERATING", "media": "MEDIA_GENERATING", "publish": "PUBLISHING"}[stage])
                    if stage == "media":
                        self._media(task)
                    elif stage == "publish":
                        self._publish(task)
                    else:
                        self._script(task)
                if task["status"] == "SCHEDULED" and now >= datetime.fromisoformat(task["scheduled_at"]):
                    if now.date().isoformat() != task["date"]:
                        self._move(task, "PAUSED", "publish day missed")
                        continue
                    try:
                        self._current(task)
                    except ValueError:
                        self._move(task, "PAUSED", "content changed after approval")
                        continue
                    if not self._approved(task, "SCRIPT") or not self._approved(task, "VIDEO") or not task["qc_passed"]:
                        self._move(task, "PAUSED", "approval gate invalid")
                        continue
                    task["receipt_id"] = str(uuid4())
                    self._move(task, "PUBLISHING")
                    self._publish(task)

    def _publish(self, task):
        self._emit("task.publish_requested.v1", {"task_id": task["task_id"], "receipt_id": task["receipt_id"],
                   "scheduled_at": task["scheduled_at"], "media_version": task["media_version"], "account_id": task["account_id"]},
                   f"{task['task_id']}:publish:{task['receipt_id']}:attempt:{task['attempts'].get('publish', 0)}")
        task.update(self.inspect(task["task_id"]))

    def _scans(self, now):
        date = now.date().isoformat()
        if now.hour < 9:
            return
        for topic in self.database.list_topics():
            row = self.connection.execute("SELECT * FROM workflow_scans WHERE topic_id=? AND scan_date=?", (topic["id"], date)).fetchone()
            if row is None:
                self.connection.execute("INSERT INTO workflow_scans(topic_id,scan_date,status,request_key) VALUES (?,?,?,?)", (topic["id"], date, "RUNNING", "initial"))
                self._scan_request(topic, date, "initial")
            elif row["status"] == "FAILED" and not row["automatic_retry"] and (now.hour, now.minute) >= (9, 10):
                self.connection.execute("UPDATE workflow_scans SET status='RUNNING',automatic_retry=1,request_key='automatic' WHERE topic_id=? AND scan_date=?", (topic["id"], date))
                self._scan_request(topic, date, "automatic")

    def _scan_request(self, topic, date, attempt):
        self._emit("workflow.search_requested.v1", {"topic_id": topic["id"], "keywords": topic["keywords"], "scan_date": date}, f"search:{topic['id']}:{date}:{attempt}")

    def scan_result(self, topic_id, date, request_key, succeeded):
        with self.atomic():
            row = self.connection.execute("SELECT * FROM workflow_scans WHERE topic_id=? AND scan_date=?", (topic_id, date)).fetchone()
            if not row or row["status"] != "RUNNING" or request_key != f"search:{topic_id}:{date}:{row['request_key']}":
                return False
            self.connection.execute("UPDATE workflow_scans SET status=? WHERE topic_id=? AND scan_date=?", ("SUCCEEDED" if succeeded else "FAILED", topic_id, date))
            return True

    def retry_search(self, topic_id):
        with self.atomic():
            date = self.now().date().isoformat()
            row = self.connection.execute("SELECT * FROM workflow_scans WHERE topic_id=? AND scan_date=?", (topic_id, date)).fetchone()
            if not row or row["status"] != "FAILED" or row["manual_retry"]:
                raise ValueError("manual retry unavailable")
            topic = next(topic for topic in self.database.list_topics() if topic["id"] == topic_id)
            self.connection.execute("UPDATE workflow_scans SET status='RUNNING',manual_retry=1,request_key='manual' WHERE topic_id=? AND scan_date=?", (topic_id, date))
            self._scan_request(topic, date, "manual")

    def pending(self):
        events = [json.loads(row[0]) for row in self.connection.execute("SELECT payload FROM workflow_outbox WHERE delivered=0 ORDER BY rowid")]
        valid = []
        for event in events:
            expected = {"task.script_requested.v1": "SCRIPT_GENERATING", "task.media_requested.v1": "MEDIA_GENERATING", "task.publish_requested.v1": "PUBLISHING"}.get(event["event_type"])
            if expected:
                task = self.inspect(event["data"]["task_id"])
                if task["status"] != expected or task.get("active_request_key") != event["idempotency_key"]:
                    continue
                source = self.database.get_task(task["task_id"])
                if source["current_script_version"] not in (None, task["script_version"]):
                    continue
                if expected != "SCRIPT_GENERATING" and (source["risk_level"] != task["risk_level"] or task["risk_level"] == "BLOCKED"):
                    continue
                if expected == "PUBLISHING" and source["current_media_version"] != task["media_version"]:
                    continue
            if event["event_type"] == "workflow.search_requested.v1":
                data = event["data"]
                row = self.connection.execute("SELECT * FROM workflow_scans WHERE topic_id=? AND scan_date=?", (data["topic_id"], data["scan_date"])).fetchone()
                if not row or row["status"] != "RUNNING" or event["idempotency_key"] != f"search:{data['topic_id']}:{data['scan_date']}:{row['request_key']}":
                    continue
            valid.append(event)
        return valid

    def acknowledge(self, event_id):
        with self.atomic():
            self.connection.execute("UPDATE workflow_outbox SET delivered=1 WHERE event_id=?", (event_id,))
