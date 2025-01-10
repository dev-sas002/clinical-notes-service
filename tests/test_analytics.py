"""Analytics correctness: the aggregation, the windows, and the cache."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.cache import InMemoryTTLCache, NullCache, stats_cache_key
from app.core.tenancy import tenant_scope
from app.core.time_ranges import resolve_range
from app.repositories.care_notes import CareNoteRepository
from app.schemas.analytics import DateRangePreset
from app.services.analytics import AnalyticsService
from tests.conftest import NOW, TENANT_A, auth

# --- aggregation arithmetic (no database needed) ------------------------


def test_fold_sums_each_dimension_to_the_same_total():
    """Every breakdown must sum to total_notes; that is the invariant."""
    rollup = [
        (11, "medication", 1, 3),
        (11, "observation", 3, 2),
        (12, "treatment", 5, 4),
        (12, "observation", 3, 1),
    ]
    stats = AnalyticsService._fold(
        tenant_id=1, rollup=rollup, unique_patients=5, start=None, end=None
    )

    assert stats.total_notes == 10
    assert sum(stats.by_category.values()) == 10
    assert sum(stats.by_priority.values()) == 10
    assert sum(stats.by_facility.values()) == 10
    assert stats.by_category == {"medication": 3, "observation": 3, "treatment": 4}
    assert stats.by_facility == {11: 5, 12: 5}
    assert stats.avg_notes_per_patient == 2.0


def test_fold_reports_every_priority_even_when_unused():
    stats = AnalyticsService._fold(
        tenant_id=1, rollup=[(11, "medication", 1, 2)], unique_patients=1, start=None, end=None
    )
    assert set(stats.by_priority) == {1, 2, 3, 4, 5}
    assert stats.by_priority[4] == 0


def test_fold_tolerates_an_out_of_range_priority():
    """The original code raised KeyError on any priority outside 1..5."""
    stats = AnalyticsService._fold(
        tenant_id=1, rollup=[(11, "medication", 9, 2)], unique_patients=1, start=None, end=None
    )
    assert stats.by_priority[9] == 2
    assert stats.total_notes == 2


def test_fold_handles_no_data_without_dividing_by_zero():
    stats = AnalyticsService._fold(tenant_id=1, rollup=[], unique_patients=0, start=None, end=None)
    assert stats.total_notes == 0
    assert stats.avg_notes_per_patient == 0


# --- date ranges --------------------------------------------------------


def test_today_is_a_half_open_day():
    now = datetime(2026, 6, 15, 13, 45)
    start, end = resolve_range(DateRangePreset.TODAY, now)
    assert start == datetime(2026, 6, 15)
    assert end == datetime(2026, 6, 16)
    assert end - start == timedelta(days=1)


def test_this_week_starts_on_monday():
    # 2026-06-15 is a Monday; check from the Wednesday.
    start, end = resolve_range(DateRangePreset.THIS_WEEK, datetime(2026, 6, 17, 9, 0))
    assert start == datetime(2026, 6, 15)
    assert end == datetime(2026, 6, 22)


def test_this_month_spans_exactly_the_month():
    start, end = resolve_range(DateRangePreset.THIS_MONTH, datetime(2026, 2, 9))
    assert start == datetime(2026, 2, 1)
    assert end == datetime(2026, 3, 1)


def test_this_month_handles_december_rollover():
    start, end = resolve_range(DateRangePreset.THIS_MONTH, datetime(2026, 12, 20))
    assert start == datetime(2026, 12, 1)
    assert end == datetime(2027, 1, 1)


def test_this_year_spans_the_year():
    start, end = resolve_range(DateRangePreset.THIS_YEAR, datetime(2026, 8, 3))
    assert start == datetime(2026, 1, 1)
    assert end == datetime(2027, 1, 1)


def test_all_time_is_unbounded():
    assert resolve_range(DateRangePreset.ALL_TIME, NOW) == (None, None)


# --- the service against a real database --------------------------------


async def test_stats_match_the_seeded_data(session, seeded):
    with tenant_scope(TENANT_A):
        stats = await AnalyticsService(CareNoteRepository(session)).care_stats()

    notes = seeded["a"]
    assert stats.total_notes == len(notes)
    assert stats.unique_patients == len({n.patient_id for n in notes})
    assert stats.by_category == {"medication": 2, "observation": 3, "treatment": 1}
    assert stats.by_facility == {11: 3, 12: 3}


async def test_stats_agree_with_a_naive_python_count(session, seeded):
    """Differential test: SQL rollup vs counting the fixtures in Python."""
    notes = seeded["a"]
    expected_category: dict[str, int] = {}
    for note in notes:
        expected_category[note.category] = expected_category.get(note.category, 0) + 1

    with tenant_scope(TENANT_A):
        stats = await AnalyticsService(CareNoteRepository(session)).care_stats()

    assert stats.by_category == expected_category
    assert stats.total_notes == len(notes)


async def test_window_is_half_open(session, seeded):
    """A note exactly on `end` is excluded; one exactly on `start` is included."""
    with tenant_scope(TENANT_A):
        service = AnalyticsService(CareNoteRepository(session))
        inclusive_start = await service.care_stats(start=NOW, end=NOW + timedelta(seconds=1))
        exclusive_end = await service.care_stats(start=NOW - timedelta(days=5), end=NOW)

    on_boundary = len([n for n in seeded["a"] if n.created_at == NOW])
    assert inclusive_start.total_notes == on_boundary
    assert exclusive_end.total_notes == len(seeded["a"]) - on_boundary


async def test_facility_filter_narrows_the_result(session, seeded):
    with tenant_scope(TENANT_A):
        stats = await AnalyticsService(CareNoteRepository(session)).care_stats(facility_ids=[11])
    assert stats.by_facility == {11: 3}
    assert stats.total_notes == 3


async def test_empty_window_returns_zeroes_not_an_error(session, seeded):
    with tenant_scope(TENANT_A):
        stats = await AnalyticsService(CareNoteRepository(session)).care_stats(
            start=NOW + timedelta(days=365), end=NOW + timedelta(days=366)
        )
    assert stats.total_notes == 0
    assert stats.by_category == {}
    assert stats.avg_notes_per_patient == 0


async def test_rollup_issues_one_query_per_dimension_set(session, seeded):
    """The rollup is a single grouped query, not one per dimension.

    Guards the optimisation: if someone reintroduces per-dimension queries the
    statement count goes up and this fails.
    """
    statements: list[str] = []
    from sqlalchemy import event

    # AsyncSession.get_bind() already returns the underlying sync Engine.
    bind = session.get_bind()

    @event.listens_for(bind, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        if "care_notes" in statement.lower():
            statements.append(statement)

    try:
        with tenant_scope(TENANT_A):
            await AnalyticsService(CareNoteRepository(session)).care_stats()
    finally:
        event.remove(bind, "before_cursor_execute", _record)

    # One grouped rollup + one distinct-patient count. The original did four.
    assert len(statements) == 2, f"expected 2 queries, got {len(statements)}"


# --- caching ------------------------------------------------------------


def test_cache_key_leads_with_the_tenant():
    key = stats_cache_key(7, (11, 12), "s", "e")
    assert key.startswith("stats:t7:")
    assert stats_cache_key(7, (12, 11), "s", "e") == key, "key should be order-insensitive"
    assert stats_cache_key(8, (11, 12), "s", "e") != key


def test_null_cache_never_stores():
    cache = NullCache()
    cache.set("k", "v")
    assert cache.get("k") is None


def test_ttl_cache_expires_entries():
    cache = InMemoryTTLCache(ttl_seconds=0.0)
    cache.set("k", "v")
    assert cache.get("k") is None


def test_ttl_cache_evicts_least_recently_used():
    cache = InMemoryTTLCache(ttl_seconds=60, max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # 'a' becomes most recently used
    cache.set("c", 3)  # evicts 'b'
    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


async def test_second_identical_request_is_served_from_cache(session, seeded):
    cache = InMemoryTTLCache(ttl_seconds=60)
    with tenant_scope(TENANT_A):
        service = AnalyticsService(CareNoteRepository(session), cache)
        first = await service.care_stats()
        second = await service.care_stats()
    assert first == second
    assert len(cache) == 1


# --- the HTTP surface ---------------------------------------------------


async def test_care_stats_endpoint_returns_the_expected_shape(client, seeded):
    response = await client.get(
        "/api/care-stats", headers=auth(TENANT_A), params={"range": "all_time"}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "tenant_id",
        "total_notes",
        "unique_patients",
        "avg_notes_per_patient",
        "by_category",
        "by_priority",
        "by_facility",
        "date_range",
    }
    assert body["total_notes"] == len(seeded["a"])


async def test_explicit_dates_must_be_supplied_as_a_pair(client, seeded):
    response = await client.get(
        "/api/care-stats", headers=auth(TENANT_A), params={"start_date": "2026-06-01T00:00:00"}
    )
    assert response.status_code == 400


async def test_end_date_must_follow_start_date(client, seeded):
    response = await client.get(
        "/api/care-stats",
        headers=auth(TENANT_A),
        params={"start_date": "2026-06-10T00:00:00", "end_date": "2026-06-01T00:00:00"},
    )
    assert response.status_code == 400


async def test_unknown_range_preset_is_rejected(client, seeded):
    response = await client.get(
        "/api/care-stats", headers=auth(TENANT_A), params={"range": "last_fortnight"}
    )
    assert response.status_code == 422


async def test_timezone_aware_dates_are_accepted(client, seeded):
    response = await client.get(
        "/api/care-stats",
        headers=auth(TENANT_A),
        params={
            "start_date": "2026-06-01T00:00:00+00:00",
            "end_date": "2026-07-01T00:00:00+00:00",
        },
    )
    assert response.status_code == 200
