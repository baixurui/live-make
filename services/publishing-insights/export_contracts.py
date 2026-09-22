"""Export consumer routes and the proposed business API context contract."""
import json
from pathlib import Path
import tempfile

from publishing.api import Settings, create_app
from publishing.models import TaskContext


ROOT = Path(__file__).resolve().parents[2]


class UnusedSource:
    def get(self, task_id):
        raise RuntimeError("OpenAPI export never reads business state")


def document():
    with tempfile.TemporaryDirectory() as directory:
        settings = Settings(str(Path(directory) / "schema.sqlite3"), "contract-export-only-token")
        schema = create_app(settings, source=UnusedSource(), worker_enabled=False).openapi()
    schema["components"]["schemas"]["TaskContext"] = TaskContext.model_json_schema()
    schema["paths"]["/api/v1/internal/tasks/{task_id}/publishing-context"] = {
        "get": {
            "summary": "Business API: trusted current publishing context",
            "description": "Provider-owned integration endpoint; business API must implement before live integration.",
            "operationId": "get_publishing_context",
            "security": [{"HTTPBearer": []}],
            "parameters": [{"name": "task_id", "in": "path", "required": True,
                            "schema": {"type": "string", "minLength": 1}}],
            "responses": {
                "200": {"description": "Authoritative task, approval, QC and scheduling state",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TaskContext"}}}},
                "401": {"description": "Missing or invalid service identity"},
                "404": {"description": "Unknown task"},
                "503": {"description": "Business state unavailable"},
            },
        }
    }
    return schema


if __name__ == "__main__":
    destination = ROOT / "contracts/publishing-insights.openapi.json"
    destination.write_text(json.dumps(document(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
