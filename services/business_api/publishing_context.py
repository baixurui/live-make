"""Read current approval evidence without trusting cached workflow votes."""
import json


def publishing_context(connection, task_id):
    if connection.in_transaction:
        raise RuntimeError("publishing context requires an idle connection")
    connection.execute("BEGIN")
    try:
        source = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if source is None:
            raise KeyError("task not found")
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_tasks'").fetchone():
            raise ValueError("workflow state is unavailable")
        row = connection.execute("SELECT payload FROM workflow_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("task is not registered with workflow")
        task = json.loads(row[0])
        if not task.get("scheduled_at"):
            raise ValueError("task is not scheduled")
        media_version = source["current_media_version"]
        if type(media_version) is not int or media_version < 1:
            raise ValueError("current media version is unavailable")
        current = (source["current_script_version"] == task["script_version"]
                   and media_version == task["media_version"]
                   and source["risk_level"] == task["risk_level"]
                   and source["status"] == task["status"])
        for table, version in (("script_versions", task["script_version"]), ("media_versions", media_version)):
            current = current and bool(connection.execute(
                f"SELECT 1 FROM {table} WHERE task_id=? AND version=?", (task_id, version)).fetchone())
        votes = {"SCRIPT": {}, "VIDEO": {}}
        for vote in connection.execute(
            """SELECT a.* FROM approvals a JOIN accounts u ON u.id=a.decided_by
               WHERE a.task_id=? AND u.enabled=1 ORDER BY a.created_at,a.id""", (task_id,)
        ):
            kind = vote["kind"]
            if kind not in votes or vote["script_version"] != task["script_version"]:
                continue
            if kind == "VIDEO" and vote["media_version"] != media_version:
                continue
            previous = votes[kind].get(vote["decided_by"])
            if previous is None or vote["decision"] == "REJECTED":
                votes[kind][vote["decided_by"]] = vote["decision"]
        def approved(kind):
            decisions = list(votes[kind].values())
            return bool(current and source["risk_level"] != "BLOCKED"
                        and "REJECTED" not in decisions
                        and decisions.count("APPROVED") >= (2 if source["risk_level"] == "HIGH" else 1))
        return {
            "task_id": task_id, "account_id": task["account_id"], "status": source["status"],
            "scheduled_at": task["scheduled_at"], "media_version": media_version,
            "approved_media_version": task["media_version"],
            "script_approved": approved("SCRIPT"), "video_approved": approved("VIDEO"),
            "qc_passed": bool(current and task["qc_passed"]), "risk_level": source["risk_level"],
            "qc_score": None, "review_score": None,
        }
    finally:
        connection.rollback()  # Read transaction only.
