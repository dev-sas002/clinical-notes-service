"""FastAPI dependencies: session, tenant context, services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.cache import AnalyticsCache, NullCache
from app.core.tenancy import tenant_scope
from app.db.session import get_session_factory
from app.repositories.care_notes import CareNoteRepository
from app.services.analytics import AnalyticsService
from app.services.care_notes import CareNoteService

TENANT_HEADER = "X-Tenant-ID"


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_tenant_id(
    x_tenant_id: Annotated[
        int | None,
        Header(
            alias=TENANT_HEADER,
            description="Identifies the calling tenant. Required on all data endpoints.",
        ),
    ] = None,
) -> int:
    """Resolve the calling tenant from the request header.

    In production this value would come from a verified JWT claim rather than a
    client-supplied header; the header stands in for that here so the service
    runs without an identity provider. Everything downstream is unaffected --
    it reads the tenant from the context, not from the transport.
    """
    if x_tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing required {TENANT_HEADER} header.",
        )
    if x_tenant_id < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{TENANT_HEADER} must be a positive integer.",
        )
    return x_tenant_id


async def tenant_context(
    tenant_id: Annotated[int, Depends(get_tenant_id)],
) -> AsyncIterator[int]:
    """Bind the tenant for the lifetime of the request.

    Declared as a router-level dependency so it applies to every data endpoint
    without each one opting in.
    """
    with tenant_scope(tenant_id):
        yield tenant_id


def get_cache(request: Request) -> AnalyticsCache:
    """Return the process-wide analytics cache built at startup."""
    return getattr(request.app.state, "analytics_cache", None) or NullCache()


def get_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CareNoteRepository:
    return CareNoteRepository(session)


def get_care_note_service(
    repository: Annotated[CareNoteRepository, Depends(get_repository)],
) -> CareNoteService:
    return CareNoteService(repository)


def get_analytics_service(
    repository: Annotated[CareNoteRepository, Depends(get_repository)],
    cache: Annotated[AnalyticsCache, Depends(get_cache)],
) -> AnalyticsService:
    return AnalyticsService(repository, cache)


def parse_facility_ids(
    facility_ids: Annotated[
        str | None,
        Query(description="Comma-separated facility ids, e.g. `11,12`."),
    ] = None,
) -> list[int] | None:
    """Parse and validate the comma-separated facility filter."""
    if not facility_ids:
        return None
    try:
        parsed = [int(part.strip()) for part in facility_ids.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="facility_ids must be a comma-separated list of integers.",
        ) from None
    if not parsed:
        return None
    if len(parsed) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At most 100 facility ids may be supplied.",
        )
    return parsed


def get_page_size(
    page_size: Annotated[int | None, Query(ge=1)] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> int:
    """Clamp the requested page size to the configured maximum."""
    settings = settings or get_settings()
    if page_size is None:
        return settings.default_page_size
    return min(page_size, settings.max_page_size)


TenantId = Annotated[int, Depends(tenant_context)]
CareNoteServiceDep = Annotated[CareNoteService, Depends(get_care_note_service)]
AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]
FacilityIds = Annotated[list[int] | None, Depends(parse_facility_ids)]
PageSize = Annotated[int, Depends(get_page_size)]
