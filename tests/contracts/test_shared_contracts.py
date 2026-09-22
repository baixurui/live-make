from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SharedContractTests(unittest.TestCase):
    def test_risk_levels_match_the_confirmed_mvp(self) -> None:
        contract = json.loads((ROOT / "contracts" / "enums.json").read_text(encoding="utf-8"))

        self.assertEqual(contract["risk_levels"], ["LOW", "HIGH", "BLOCKED"])

    def test_openapi_exposes_core_mvp_paths(self) -> None:
        openapi = (ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8")

        for path in ("/api/v1/auth/login", "/api/v1/topics", "/api/v1/tasks", "/api/v1/metrics"):
            self.assertIn(path, openapi)

    def test_event_schemas_use_the_shared_envelope(self) -> None:
        schema_directory = ROOT / "contracts" / "events" / "v1"
        schemas = list(schema_directory.glob("*.json"))

        self.assertGreaterEqual(len(schemas), 12)
        for schema_path in schemas:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            self.assertEqual(
                schema["required"],
                ["event_id", "event_type", "occurred_at", "correlation_id", "idempotency_key", "data"],
            )


if __name__ == "__main__":
    unittest.main()
