"""Liveness and readiness endpoints.

Not tenant-scoped: these are for the load balancer, not for callers.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    """Return OK if the process is up. Does not touch the database."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def ready(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Return OK once the database answers a trivial query."""
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}


@router.get("/health/live", status_code=status.HTTP_200_OK, include_in_schema=False)
async def live() -> dict[str, str]:
    """Alias for container healthchecks."""
    return {"status": "ok"}
