"""Application factory and ASGI entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.routers import analytics, care_notes, health
from app.config import Settings, get_settings
from app.core.cache import build_cache
from app.core.logging import configure_logging
from app.core.tenancy import install_tenant_guard
from app.db.session import create_schema, dispose_engine

logger = logging.getLogger(__name__)

DESCRIPTION = """
Multi-tenant API for care notes and the analytics computed over them.

Every data endpoint requires an `X-Tenant-ID` header. Tenant filtering is
applied by a SQLAlchemy hook rather than by each query, so a request can only
ever see the rows belonging to the tenant it declared.
"""


def _lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        install_tenant_guard()
        app.state.analytics_cache = build_cache(
            settings.analytics_cache_ttl_seconds,
            settings.analytics_cache_max_entries,
        )
        # Create missing tables only. Seeding is a separate, explicit command
        # (`python -m app.seed`) -- it is not startup's job, and the original
        # version's drop-and-reseed-100k-rows startup made every restart both
        # destructive and minutes long.
        await create_schema()
        logger.info("%s started (environment=%s)", settings.app_name, settings.environment)
        try:
            yield
        finally:
            await dispose_engine()

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application."""
    settings = settings or get_settings()
    install_tenant_guard()

    app = FastAPI(
        title="Care Notes API",
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=_lifespan(settings),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Tenant-ID"],
    )

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(care_notes.router)
    app.include_router(analytics.router)
    return app


app = create_app()
