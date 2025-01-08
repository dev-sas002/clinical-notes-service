"""Resolution of named date-range presets into half-open windows.

Kept pure and free of FastAPI and SQLAlchemy so the calendar edge cases can be
tested directly.

Every window is half-open: ``start <= created_at < end``. The original code
built closed windows ending at ``23:59:59.999999``, which silently dropped any
row written in the final microsecond of a day and made "today" and "this week"
overlap at the boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.schemas.analytics import DateRangePreset


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    Naive-UTC matches how timestamps are stored. ``datetime.utcnow()`` is
    deprecated from Python 3.12, hence the explicit conversion.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _start_of_day(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def resolve_range(
    preset: DateRangePreset,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Return the half-open ``[start, end)`` window for ``preset``.

    ``ALL_TIME`` returns ``(None, None)`` -- an genuinely unbounded window --
    rather than ``datetime.min``/``datetime.max``, which are not representable
    in every backend and forced a full index scan in SQLite.
    """
    now = now or utcnow()
    today = _start_of_day(now)

    if preset is DateRangePreset.TODAY:
        return today, today + timedelta(days=1)
    if preset is DateRangePreset.THIS_WEEK:
        start = today - timedelta(days=now.weekday())
        return start, start + timedelta(days=7)
    if preset is DateRangePreset.THIS_MONTH:
        start = today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        return start, end
    if preset is DateRangePreset.THIS_YEAR:
        start = today.replace(month=1, day=1)
        return start, start.replace(year=start.year + 1)
    if preset is DateRangePreset.ALL_TIME:
        return None, None

    raise ValueError(f"Unsupported date range preset: {preset!r}")
