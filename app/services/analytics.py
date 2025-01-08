"""Analytics business logic."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.core.cache import AnalyticsCache, NullCache, stats_cache_key
from app.core.tenancy import current_tenant_id
from app.models.care_note import PRIORITY_MAX, PRIORITY_MIN
from app.repositories.care_notes import CareNoteRepository
from app.schemas.analytics import CareStats, DateRange


class AnalyticsService:
    """Computes care statistics for the tenant in the current context."""

    def __init__(
        self,
        repository: CareNoteRepository,
        cache: AnalyticsCache | None = None,
    ) -> None:
        self._repo = repository
        # `is not None`, not `or`: an empty InMemoryTTLCache is falsy
        # (it defines __len__), so `or` would silently discard a real cache.
        self._cache = cache if cache is not None else NullCache()

    async def care_stats(
        self,
        *,
        facility_ids: Sequence[int] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> CareStats:
        """Return aggregated stats for the half-open window ``[start, end)``."""
        tenant_id = current_tenant_id()
        facilities = tuple(sorted(set(facility_ids))) if facility_ids else None

        key = stats_cache_key(tenant_id, facilities, start, end)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        rollup = await self._repo.rollup(facility_ids=facilities, start=start, end=end)
        unique_patients = await self._repo.count_distinct_patients(
            facility_ids=facilities, start=start, end=end
        )

        stats = self._fold(
            tenant_id=tenant_id,
            rollup=rollup,
            unique_patients=unique_patients,
            start=start,
            end=end,
        )
        self._cache.set(key, stats)
        return stats

    @staticmethod
    def _fold(
        *,
        tenant_id: int,
        rollup: Sequence[tuple[int, str, int, int]],
        unique_patients: int,
        start: datetime | None,
        end: datetime | None,
    ) -> CareStats:
        """Fold the grouped rows into the response shape.

        Pure and static, so the aggregation arithmetic is testable without a
        database.
        """
        by_category: dict[str, int] = {}
        by_priority: dict[int, int] = dict.fromkeys(range(PRIORITY_MIN, PRIORITY_MAX + 1), 0)
        by_facility: dict[int, int] = {}
        total = 0

        for facility_id, category, priority, count in rollup:
            total += count
            by_category[category] = by_category.get(category, 0) + count
            by_facility[facility_id] = by_facility.get(facility_id, 0) + count
            # Tolerate out-of-range priorities rather than raising: the
            # original code did `by_priority[note.priority] += 1` against a
            # fixed 1..5 dict and blew up on any legacy row outside it.
            by_priority[priority] = by_priority.get(priority, 0) + count

        avg = round(total / unique_patients, 2) if unique_patients else 0.0

        return CareStats(
            tenant_id=tenant_id,
            total_notes=total,
            unique_patients=unique_patients,
            avg_notes_per_patient=avg,
            by_category=by_category,
            by_priority=by_priority,
            by_facility=by_facility,
            date_range=DateRange(start=start, end=end),
        )
