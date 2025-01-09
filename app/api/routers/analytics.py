"""Analytics endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AnalyticsServiceDep, FacilityIds, tenant_context
from app.core.time_ranges import resolve_range
from app.schemas.analytics import CareStats, DateRangePreset

router = APIRouter(
    prefix="/api",
    tags=["analytics"],
    dependencies=[Depends(tenant_context)],
)


@router.get("/care-stats", response_model=CareStats, summary="Aggregated care statistics")
async def care_stats(
    service: AnalyticsServiceDep,
    facility_ids: FacilityIds = None,
    range: Annotated[
        DateRangePreset,
        Query(description="Named window; ignored when start_date/end_date are given."),
    ] = DateRangePreset.TODAY,
    start_date: Annotated[
        datetime | None, Query(description="ISO-8601 inclusive lower bound.")
    ] = None,
    end_date: Annotated[
        datetime | None, Query(description="ISO-8601 exclusive upper bound.")
    ] = None,
) -> CareStats:
    """Aggregate the calling tenant's notes over a window.

    Supply either a named ``range`` or an explicit ``start_date``/``end_date``
    pair. The window is half-open: ``start <= created_at < end``.
    """
    if (start_date is None) != (end_date is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date and end_date must be supplied together.",
        )

    if start_date is not None and end_date is not None:
        if end_date <= start_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="end_date must be later than start_date.",
            )
        start, end = start_date, end_date
    else:
        start, end = resolve_range(range)

    # Timestamps are stored naive-UTC; normalise any offset-aware input so the
    # comparison does not raise.
    start = _to_naive_utc(start)
    end = _to_naive_utc(end)

    return await service.care_stats(facility_ids=facility_ids, start=start, end=end)


def _to_naive_utc(moment: datetime | None) -> datetime | None:
    if moment is None or moment.tzinfo is None:
        return moment

    return moment.astimezone(UTC).replace(tzinfo=None)
