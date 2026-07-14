"""FastAPI application factory for mockops."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from agent_governor import __version__
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.mockops.faults import FaultInjector, FaultMiddleware
from agent_governor.mockops.routes import admin_router, router
from agent_governor.mockops.state import build_store


class HealthResponse(BaseModel):
    status: str
    version: str
    seed: int


def create_app(settings: MockOpsSettings | None = None) -> FastAPI:
    settings = settings or MockOpsSettings()
    app = FastAPI(
        title="mockops",
        version=__version__,
        description="Deterministic mock ITSM/ops estate with controllable fault injection.",
    )
    app.state.settings = settings
    app.state.store = build_store(settings.seed)
    app.state.faults = FaultInjector(settings.faults.model_copy(update={"seed": settings.seed}))

    @app.get("/health", tags=["admin"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__, seed=settings.seed)

    app.include_router(router)
    app.include_router(admin_router)
    app.add_middleware(FaultMiddleware)
    return app
