"""Schemas for the analytics endpoint."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DateRangePreset(StrEnum):
    """Named windows the client can ask for instead of explicit dates."""

    TODAY = "today"
    THIS_WEEK = "this_week"
    THIS_MONTH = "this_month"
    THIS_YEAR = "this_year"
    ALL_TIME = "all_time"


class DateRange(BaseModel):
    """The half-open window ``[start, end)`` the stats were computed over."""

    start: datetime | None = Field(
        default=None, description="Inclusive lower bound; null means unbounded."
    )
    end: datetime | None = Field(
        default=None, description="Exclusive upper bound; null means unbounded."
    )


class CareStats(BaseModel):
    """Aggregated care-note statistics for one tenant over one window."""

    tenant_id: int
    total_notes: int
    unique_patients: int
    avg_notes_per_patient: float
    by_category: dict[str, int]
    by_priority: dict[int, int]
    by_facility: dict[int, int]
    date_range: DateRange
