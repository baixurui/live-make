import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hmac
import json
import logging
import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .adapters import HttpTaskSource, SimulatedPublisher
from .insights import Insights
from .models import (DependencyUnavailable, DomainError, ErrorResponse, FailedEvent,
                     MetricsResponse, PublishedEvent, PublishRequest, Receipt, utc_now)
from .service import PublishingService
from .storage import Store


logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Settings:
    database_path: str
    workflow_token: str
    metrics_tokens: dict[str, frozenset[str]] = field(default_factory=dict)
    business_api_url: str = ""
    business_api_token: str = ""
    simulation_seed: str = "live-make-v1"
    worker_interval: float = 5

    def __post_init__(self):
        if len(self.workflow_token) < 16:
            raise ValueError("workflow token must contain at least 16 characters")
        if self.worker_interval <= 0:
            raise ValueError("worker interval must be positive")
        for token, accounts in self.metrics_tokens.items():
            if len(token) < 16 or token == self.workflow_token or not accounts:
                raise ValueError("metrics tokens must be distinct and scoped to accounts")
            if any(not isinstance(account, str) or not account.strip() for account in accounts):
                raise ValueError("metrics accounts must be non-empty strings")


def create_app(settings: Settings, *, source=None, publisher=None, clock=utc_now,
               worker_enabled=True) -> FastAPI:
    store = Store(settings.database_path)
    source = source if source is not None else HttpTaskSource(settings.business_api_url, settings.business_api_token)
    publisher = publisher if publisher is not None else SimulatedPublisher()
    service = PublishingService(store, source, publisher, settings.simulation_seed, clock)
    insights = Insights(store, clock)

    async def worker(stop):
        while not stop.is_set():
            try:
                await asyncio.to_thread(service.recover)
                await asyncio.to_thread(insights.maintain)
            except Exception:
                logger.exception("publishing maintenance failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_interval)
            except TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        task = asyncio.create_task(worker(stop)) if worker_enabled else None
        try:
            yield
        finally:
            stop.set()
            if task:
                await task

    app = FastAPI(title="Live Make Publishing & Insights", version="0.1.0", lifespan=lifespan)
    app.state.service, app.state.insights, app.state.store = service, insights, store

    def workflow(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), settings.workflow_token.encode()):
            raise HTTPException(401, "workflow authentication required", headers={"WWW-Authenticate": "Bearer"})

    def metrics_scope(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is not None:
            for token, accounts in settings.metrics_tokens.items():
                if hmac.compare_digest(credentials.credentials.encode(), token.encode()):
                    return accounts
        raise HTTPException(401, "metrics authentication required", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse({"code": error.code}, status_code=error.status)

    @app.exception_handler(DependencyUnavailable)
    async def dependency_error(request, error):
        return JSONResponse({"code": "BUSINESS_STATE_UNAVAILABLE"}, status_code=503)

    @app.exception_handler(sqlite3.OperationalError)
    async def storage_error(request, error):
        logger.error("publishing database operation unavailable")
        return JSONResponse({"code": "STORAGE_UNAVAILABLE"}, status_code=503)

    @app.get("/healthz", tags=["health"])
    def health():
        with store.connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok"}

    @app.post("/api/v1/internal/publishing/requests", response_model=Receipt,
              responses={202: {"model": Receipt}, 409: {"model": ErrorResponse},
                         503: {"model": ErrorResponse}}, dependencies=[Depends(workflow)])
    def publish(request: PublishRequest, response: Response):
        receipt = service.submit(request)
        if receipt.status == "PROCESSING":
            response.status_code = 202
        return receipt

    @app.get("/api/v1/internal/publishing/receipts/{receipt_id}", response_model=Receipt,
             responses={404: {"model": ErrorResponse}}, dependencies=[Depends(workflow)])
    def receipt(receipt_id: str):
        return service.receipt(receipt_id)

    @app.get("/api/v1/internal/publishing/events", response_model=list[PublishedEvent | FailedEvent],
             dependencies=[Depends(workflow)])
    def events(limit: int = Query(100, ge=1, le=100)):
        return [PublishedEvent.model_validate_json(json.dumps(event))
                if event["event_type"] == "task.published.v1"
                else FailedEvent.model_validate_json(json.dumps(event))
                for event in service.events(limit)]

    @app.post("/api/v1/internal/publishing/events/{event_id}/ack", status_code=204,
              responses={404: {"model": ErrorResponse}}, dependencies=[Depends(workflow)])
    def acknowledge(event_id: str):
        service.acknowledge(event_id)
        return Response(status_code=204)

    @app.get("/api/v1/metrics", response_model=MetricsResponse)
    def metrics(account_id: str = Query(..., min_length=1, max_length=256),
                accounts=Depends(metrics_scope)):
        if account_id not in accounts:
            raise HTTPException(403, "account is outside the credential scope")
        return insights.metrics(account_id)

    return app
