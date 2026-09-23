import unittest
from datetime import datetime, timedelta, timezone

from services.business_api.app import BusinessApi
from services.business_api.db import Database
from services.business_api.security import hash_password, verify_password
from services.business_api.state_machine import InvalidTransition, validate_transition


class BusinessApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Database()
        self.admin = self.database.create_account("admin", "administrator-password", is_admin=True)
        self.member = self.database.create_account("member", "member-password-1")
        self.api = BusinessApi(self.database)
        self.admin_token = self._login("admin", "administrator-password")
        self.member_token = self._login("member", "member-password-1")

    def tearDown(self) -> None:
        self.database.close()

    def _login(self, username: str, password: str) -> str:
        response = self.api.request("POST", "/api/v1/auth/login", body={"username": username, "password": password})
        self.assertEqual(response.status, 200)
        return response.body["access_token"]

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_password_hash_is_one_way_and_account_response_has_no_secret(self) -> None:
        encoded = hash_password("a-long-password")
        self.assertTrue(verify_password("a-long-password", encoded))
        self.assertFalse(verify_password("wrong-password", encoded))
        account = self.api.request("POST", "/api/v1/admin/accounts", self._headers(self.admin_token), {"username": "new-user", "password": "new-user-password"})
        self.assertEqual(account.status, 201)
        self.assertNotIn("password", account.body)
        self.assertNotIn("password_hash", account.body)

    def test_admin_can_disable_and_reset_an_account(self) -> None:
        created = self.api.request("POST", "/api/v1/admin/accounts", self._headers(self.admin_token), {"username": "managed-user", "password": "managed-password"})
        account_id = created.body["id"]
        disabled = self.api.request("PATCH", f"/api/v1/admin/accounts/{account_id}", self._headers(self.admin_token), {"enabled": False})
        self.assertEqual(disabled.status, 200)
        self.assertEqual(self.api.request("POST", "/api/v1/auth/login", body={"username": "managed-user", "password": "managed-password"}).status, 401)
        reset = self.api.request("PATCH", f"/api/v1/admin/accounts/{account_id}", self._headers(self.admin_token), {"enabled": True, "password": "new-managed-password"})
        self.assertEqual(reset.status, 200)
        self.assertEqual(self.api.request("POST", "/api/v1/auth/login", body={"username": "managed-user", "password": "new-managed-password"}).status, 200)

    def test_only_admin_can_manage_topics(self) -> None:
        denied = self.api.request("POST", "/api/v1/topics", self._headers(self.member_token), {"name": "AI", "keywords": ["model"]})
        self.assertEqual(denied.status, 403)
        created = self.api.request("POST", "/api/v1/topics", self._headers(self.admin_token), {"name": "AI", "keywords": ["model"]})
        self.assertEqual(created.status, 201)
        listed = self.api.request("GET", "/api/v1/topics", self._headers(self.member_token))
        self.assertEqual(listed.status, 200)
        self.assertEqual(listed.body[0]["name"], "AI")
        disabled = self.api.request("PATCH", f"/api/v1/topics/{created.body['id']}", self._headers(self.admin_token), {"enabled": False})
        self.assertEqual(disabled.status, 200)
        self.assertEqual(self.api.request("GET", "/api/v1/topics", self._headers(self.member_token)).body, [])

    def test_versions_are_append_only_and_approvals_are_audited(self) -> None:
        task = self.database.create_task(self.admin["id"], "Demo task")
        first = self.database.add_script_version(self.member["id"], task["id"], "version one")
        second = self.database.add_script_version(self.member["id"], task["id"], "version two")
        self.assertEqual((first["version"], second["version"]), (1, 2))
        approval = self.database.add_approval(self.admin["id"], task["id"], "SCRIPT", "APPROVED", "looks good")
        self.assertEqual(approval["decision"], "APPROVED")
        audit_actions = [entry["action"] for entry in self.database.audit_for("task", task["id"])]
        self.assertIn("script_version.created", audit_actions)
        self.assertIn("approval.created", audit_actions)

    def test_only_workflow_can_transition_and_invalid_edges_are_rejected(self) -> None:
        task = self.database.create_task(self.admin["id"], "Demo task")
        forbidden = self.api.request("POST", f"/api/v1/internal/tasks/{task['id']}/transitions", self._headers(self.admin_token), {"target_status": "TOPIC_SELECTED"})
        self.assertEqual(forbidden.status, 403)
        allowed = self.api.request("POST", f"/api/v1/internal/tasks/{task['id']}/transitions", {"X-Internal-Service": "workflow"}, {"target_status": "TOPIC_SELECTED"})
        self.assertEqual(allowed.status, 204)
        with self.assertRaises(InvalidTransition):
            validate_transition("TOPIC_SELECTED", "PUBLISHED")

    def test_internal_result_services_can_append_their_own_versions(self) -> None:
        task = self.database.create_task(self.admin["id"], "Demo task")
        denied = self.api.request("POST", f"/api/v1/internal/tasks/{task['id']}/script-versions", {"X-Internal-Service": "media-production"}, {"content": "wrong owner"})
        self.assertEqual(denied.status, 403)
        script = self.api.request("POST", f"/api/v1/internal/tasks/{task['id']}/script-versions", {"X-Internal-Service": "content-intelligence"}, {"content": "approved script"})
        media = self.api.request("POST", f"/api/v1/internal/tasks/{task['id']}/media-versions", {"X-Internal-Service": "media-production"}, {"uri": "s3://video/1.mp4"})
        asset = self.api.request("POST", f"/api/v1/internal/tasks/{task['id']}/assets", {"X-Internal-Service": "media-production"}, {"kind": "thumbnail", "uri": "s3://video/thumb.jpg"})
        self.assertEqual((script.status, media.status, asset.status), (201, 201, 201))
        self.assertEqual(self.database.get_task(task["id"])["current_script_version"], 1)
        self.assertEqual(self.database.get_task(task["id"])["current_media_version"], 1)

    def test_purge_removes_only_records_older_than_seven_days(self) -> None:
        task = self.database.create_task(self.admin["id"], "Retention task")
        self.database.add_script_version(self.member["id"], task["id"], "retained script")
        self.database.add_approval(self.admin["id"], task["id"], "SCRIPT", "APPROVED")
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        self.database.connection.execute("UPDATE script_versions SET created_at = ? WHERE task_id = ?", (old, task["id"]))
        self.database.connection.execute("UPDATE approvals SET created_at = ? WHERE task_id = ?", (old, task["id"]))
        self.database.connection.execute("UPDATE audit_logs SET created_at = ? WHERE resource_id = ?", (old, task["id"]))
        self.database.connection.commit()
        self.assertGreaterEqual(self.database.purge_expired(), 3)
        self.assertEqual(self.database.connection.execute("SELECT COUNT(*) FROM script_versions WHERE task_id = ?", (task["id"],)).fetchone()[0], 0)
        self.assertEqual(self.database.connection.execute("SELECT COUNT(*) FROM approvals WHERE task_id = ?", (task["id"],)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
