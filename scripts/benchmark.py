"""Compare naive in-memory aggregation against the SQL rollup.

This replaces the original ``POST /api/run-performance-test`` endpoint, which
was wrong in two ways. It ran an unbounded benchmark loop inside a request
handler, so any caller could pin the event loop; and it compared the two
implementations over *different* windows -- the naive path got a whole day
while the "optimized" path was handed ``(test_date, test_date)``, a zero-width
range matching nothing. The speed-up it printed was mostly the cost of
scanning no rows.

Here both implementations aggregate the same half-open window, and the script
asserts they produce identical totals before reporting any timing. A benchmark
whose two sides disagree is not measuring the same thing.

    python -m scripts.benchmark --iterations 10
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from datetime import timedelta

from sqlalchemy import func, select

from app.core.logging import configure_logging
from app.core.tenancy import install_tenant_guard, tenant_scope, unscoped
from app.core.time_ranges import utcnow
from app.db.session import dispose_engine, session_scope
from app.models.care_note import CareNote
from app.repositories.care_notes import CareNoteRepository
from app.services.analytics import AnalyticsService


async def naive_stats(session, start, end) -> dict:
    """The original approach: pull every row back and count in Python."""
    stmt = select(CareNote).where(CareNote.created_at >= start, CareNote.created_at < end)
    notes = (await session.execute(stmt)).scalars().all()
    by_category: dict[str, int] = {}
    by_priority: dict[int, int] = {}
    by_facility: dict[int, int] = {}
    patients = set()
    for note in notes:
        by_category[note.category] = by_category.get(note.category, 0) + 1
        by_priority[note.priority] = by_priority.get(note.priority, 0) + 1
        by_facility[note.facility_id] = by_facility.get(note.facility_id, 0) + 1
        patients.add(note.patient_id)
    return {
        "total_notes": len(notes),
        "unique_patients": len(patients),
        "by_category": by_category,
        "by_facility": by_facility,
    }


async def _time(fn, iterations: int) -> list[float]:
    timings = []
    for _ in range(iterations):
        start = time.perf_counter()
        await fn()
        timings.append(time.perf_counter() - start)
    return timings


async def run(tenant_id: int, days: int, iterations: int) -> None:
    install_tenant_guard()
    now = utcnow()
    start, end = now - timedelta(days=days), now

    async with session_scope() as session:
        with unscoped():
            total = (await session.execute(select(func.count(CareNote.id)))).scalar_one()
        if not total:
            print("No data. Run `python -m app.seed` first.")
            return

        with tenant_scope(tenant_id):
            repo = CareNoteRepository(session)
            service = AnalyticsService(repo)  # NullCache: measure the query, not the cache

            naive = await naive_stats(session, start, end)
            optimised = await service.care_stats(start=start, end=end)

            # Both sides must agree before any timing is reported.
            assert naive["total_notes"] == optimised.total_notes, (
                f"totals differ: {naive['total_notes']} vs {optimised.total_notes}"
            )
            assert naive["unique_patients"] == optimised.unique_patients
            assert naive["by_category"] == optimised.by_category
            assert naive["by_facility"] == optimised.by_facility

            naive_times = await _time(lambda: naive_stats(session, start, end), iterations)
            opt_times = await _time(lambda: service.care_stats(start=start, end=end), iterations)

    rows_total = total
    naive_avg = statistics.mean(naive_times)
    opt_avg = statistics.mean(opt_times)

    print("\n=== Care stats aggregation benchmark ===")
    print(f"Rows in table          : {rows_total:,}")
    print(f"Tenant / window        : {tenant_id} / last {days} days")
    print(f"Rows in window (tenant): {optimised.total_notes:,}")
    print(f"Iterations             : {iterations}")
    print("Results agree          : yes")
    print()
    naive_median = statistics.median(naive_times) * 1000
    opt_median = statistics.median(opt_times) * 1000
    print(
        f"Naive (load + count in Python) : {naive_avg * 1000:8.2f} ms"
        f"  (median {naive_median:.2f} ms)"
    )
    print(
        f"SQL rollup (2 queries)         : {opt_avg * 1000:8.2f} ms  (median {opt_median:.2f} ms)"
    )
    if opt_avg > 0:
        print(f"Speed-up                       : {naive_avg / opt_avg:8.2f}x")
    print()
    print("Naive transfers every matching row into the process; the rollup")
    print("returns at most facilities x categories x priorities rows.")


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()
    configure_logging("WARNING")
    try:
        await run(args.tenant_id, args.days, args.iterations)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
