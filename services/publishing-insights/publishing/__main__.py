import json
import logging
import os

import uvicorn

from .api import Settings, create_app


def main():
    logging.basicConfig(level=logging.INFO)
    scopes = json.loads(os.environ["METRICS_TOKEN_SCOPES"])
    if not isinstance(scopes, dict) or any(not isinstance(value, list) for value in scopes.values()):
        raise ValueError("METRICS_TOKEN_SCOPES must map tokens to account lists")
    settings = Settings(
        database_path=os.environ.get("PUBLISHING_DATABASE_PATH", "data/publishing.sqlite3"),
        workflow_token=os.environ["WORKFLOW_PUBLISHING_TOKEN"],
        metrics_tokens={key: frozenset(value) for key, value in scopes.items()},
        business_api_url=os.environ["BUSINESS_API_URL"],
        business_api_token=os.environ["BUSINESS_API_TOKEN"],
        simulation_seed=os.environ.get("PUBLISHING_SIMULATION_SEED", "live-make-v1"),
        worker_interval=float(os.environ.get("PUBLISHING_WORKER_INTERVAL", "5")),
    )
    uvicorn.run(create_app(settings), host="0.0.0.0", port=int(os.environ.get("PORT", "8014")))


if __name__ == "__main__":
    main()
