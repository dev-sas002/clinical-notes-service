"""Cross-tenant isolation.

This is the file to read first. The service holds clinical records for several
organisations in one table, so the question that matters is not "does it work"
but "can tenant A ever observe tenant B". Each test below is one way a leak
could happen.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.tenancy import (
    MissingTenantContextError,
    current_tenant_id,
    tenant_scope,
    unscoped,
)
from app.models.care_note import CareNote
from app.repositories.care_notes import CareNoteRepository
from tests.conftest import TENANT_A, TENANT_B, auth

# --- the guard itself ---------------------------------------------------


async def test_query_without_tenant_context_raises(session, seeded):
    """A read with no tenant bound must fail loudly, not return everything.

    This is the whole design in one assertion: the failure mode of forgetting
    the tenant is an exception, not a full-table scan.
    """
    with pytest.raises(MissingTenantContextError):
        await session.execute(select(CareNote))


async def test_query_forgetting_the_filter_is_still_scoped(session, seeded):
    """A query written *without* any tenant predicate still comes back scoped.

    Simulates the developer mistake the design exists to neutralise.
    """
    with tenant_scope(TENANT_A):
        rows = (await session.execute(select(CareNote))).scalars().all()

    assert rows, "expected tenant A to have notes"
    assert {r.tenant_id for r in rows} == {TENANT_A}
    assert len(rows) == len(seeded["a"])


async def test_aggregates_are_scoped_too(session, seeded):
    """Column and aggregate selects are scoped, not just entity selects.

    Worth asserting separately: a mechanism that filtered `select(CareNote)`
    but not `select(func.count(...))` would leak totals while looking safe.
    """
    with tenant_scope(TENANT_A):
        count = (await session.execute(select(func.count(CareNote.id)))).scalar_one()
        grouped = (
            await session.execute(
                select(CareNote.category, func.count(CareNote.id)).group_by(CareNote.category)
            )
        ).all()
        patients = (
            await session.execute(select(func.count(func.distinct(CareNote.patient_id))))
        ).scalar_one()

    assert count == len(seeded["a"])
    assert sum(c for _, c in grouped) == len(seeded["a"])
    assert patients == len({n.patient_id for n in seeded["a"]})


async def test_scope_switches_cleanly_between_tenants(session, seeded):
    """Alternating tenants must not reuse a cached predicate.

    SQLAlchemy caches compiled statements, so a tenant id baked into a cached
    plan would be a silent, catastrophic leak. Alternate repeatedly.
    """
    for tenant, expected in [
        (TENANT_A, len(seeded["a"])),
        (TENANT_B, len(seeded["b"])),
        (TENANT_A, len(seeded["a"])),
        (TENANT_B, len(seeded["b"])),
        (TENANT_A, len(seeded["a"])),
    ]:
        with tenant_scope(tenant):
            rows = (await session.execute(select(CareNote))).scalars().all()
        assert len(rows) == expected
        assert {r.tenant_id for r in rows} == {tenant}


async def test_nested_scopes_restore_the_outer_tenant(session, seeded):
    with tenant_scope(TENANT_A):
        assert current_tenant_id() == TENANT_A
        with tenant_scope(TENANT_B):
            assert current_tenant_id() == TENANT_B
        assert current_tenant_id() == TENANT_A


async def test_unscoped_is_the_only_way_to_see_everything(session, seeded):
    with unscoped():
        total = (await session.execute(select(func.count(CareNote.id)))).scalar_one()
    assert total == len(seeded["a"]) + len(seeded["b"])


async def test_unscoped_context_has_no_current_tenant(session):
    with unscoped(), pytest.raises(MissingTenantContextError):
        current_tenant_id()


# --- the repository -----------------------------------------------------


async def test_repository_cannot_fetch_another_tenants_note(session, seeded):
    """The classic IDOR: guess an id, ask for it as the wrong tenant."""
    victim = seeded["b"][0]
    assert victim.id is not None

    with tenant_scope(TENANT_A):
        assert await CareNoteRepository(session).get(victim.id) is None

    with tenant_scope(TENANT_B):
        assert (await CareNoteRepository(session).get(victim.id)) is not None


async def test_repository_cannot_delete_another_tenants_note(session, seeded):
    """A cross-tenant delete must be a no-op, and must not destroy the row.

    The guard covers SELECT only, so DELETE is the one path where a leak could
    still hide; the repository resolves the row first for exactly that reason.
    """
    victim = seeded["b"][0]

    with tenant_scope(TENANT_A):
        assert await CareNoteRepository(session).delete(victim.id) is False

    with unscoped():
        still_there = (
            await session.execute(select(CareNote).where(CareNote.id == victim.id))
        ).scalar_one_or_none()
    assert still_there is not None, "cross-tenant delete destroyed the row"


# --- the HTTP surface ---------------------------------------------------


async def test_listing_requires_a_tenant_header(client, seeded):
    response = await client.get("/api/care-notes")
    assert response.status_code == 400
    assert "X-Tenant-ID" in response.json()["detail"]


async def test_listing_returns_only_the_callers_notes(client, seeded):
    response = await client.get(
        "/api/care-notes", headers=auth(TENANT_A), params={"page_size": 100}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == len(seeded["a"])
    assert {n["tenant_id"] for n in body["notes"]} == {TENANT_A}


async def test_listing_filtered_by_another_tenants_facility_returns_nothing(client, seeded):
    """Asking for a facility you do not own must return empty, not that facility."""
    response = await client.get(
        "/api/care-notes", headers=auth(TENANT_A), params={"facility_ids": "21"}
    )
    assert response.status_code == 200
    assert response.json()["notes"] == []


async def test_get_by_id_across_tenants_is_404(client, seeded):
    """404, not 403 -- a 403 would confirm the id exists for someone else."""
    victim = seeded["b"][0]
    response = await client.get(f"/api/care-notes/{victim.id}", headers=auth(TENANT_A))
    assert response.status_code == 404
    assert str(victim.patient_id) not in response.text


async def test_update_across_tenants_is_404_and_changes_nothing(client, session, seeded):
    victim = seeded["b"][0]
    original = victim.note_content

    response = await client.put(
        f"/api/care-notes/{victim.id}",
        headers=auth(TENANT_A),
        json={"note_content": "SYNTHETIC FIXTURE - tampered."},
    )
    assert response.status_code == 404

    session.expunge_all()
    with unscoped():
        row = (await session.execute(select(CareNote).where(CareNote.id == victim.id))).scalar_one()
    assert row.note_content == original


async def test_delete_across_tenants_is_404(client, seeded):
    victim = seeded["b"][0]
    response = await client.delete(f"/api/care-notes/{victim.id}", headers=auth(TENANT_A))
    assert response.status_code == 404


async def test_created_note_belongs_to_the_header_tenant(client, seeded):
    """The tenant comes from the context, never the body."""
    response = await client.post(
        "/api/care-notes",
        headers=auth(TENANT_A),
        json={
            "facility_id": 11,
            "patient_id": "P-T1-F11-900",
            "category": "observation",
            "priority": 2,
            "created_by": "staff_01",
            "note_content": "SYNTHETIC FIXTURE - created in test.",
        },
    )
    assert response.status_code == 201
    assert response.json()["tenant_id"] == TENANT_A


async def test_tenant_id_in_the_body_cannot_override_the_header(client, seeded):
    """Forging `tenant_id` in the payload must not place the note elsewhere.

    The create schema has no `tenant_id` field, so the value is ignored rather
    than honoured. This is the regression test for that guarantee.
    """
    response = await client.post(
        "/api/care-notes",
        headers=auth(TENANT_A),
        json={
            "tenant_id": TENANT_B,
            "facility_id": 11,
            "patient_id": "P-T1-F11-901",
            "category": "observation",
            "priority": 2,
            "created_by": "staff_01",
            "note_content": "SYNTHETIC FIXTURE - forged tenant attempt.",
        },
    )
    assert response.status_code == 201
    assert response.json()["tenant_id"] == TENANT_A, "body overrode the tenant context"


async def test_analytics_are_scoped_to_the_caller(client, seeded):
    """Two tenants asking the same question get different, correct answers."""
    params = {"range": "all_time"}
    a = (await client.get("/api/care-stats", headers=auth(TENANT_A), params=params)).json()
    b = (await client.get("/api/care-stats", headers=auth(TENANT_B), params=params)).json()

    assert a["total_notes"] == len(seeded["a"])
    assert b["total_notes"] == len(seeded["b"])
    assert a["by_facility"].keys() != b["by_facility"].keys()
    assert "21" not in a["by_facility"]
    assert "11" not in b["by_facility"]


async def test_analytics_cache_does_not_leak_between_tenants(session, seeded):
    """A shared cache with a tenant-blind key would be a leak. Prove it is not.

    Same facility filter, same window, two tenants, one cache instance.
    """
    from app.core.cache import InMemoryTTLCache
    from app.services.analytics import AnalyticsService

    cache = InMemoryTTLCache(ttl_seconds=60)
    repo = CareNoteRepository(session)

    with tenant_scope(TENANT_A):
        first = await AnalyticsService(repo, cache).care_stats()
    with tenant_scope(TENANT_B):
        second = await AnalyticsService(repo, cache).care_stats()

    assert first.tenant_id == TENANT_A
    assert second.tenant_id == TENANT_B
    assert len(cache) == 2, "tenants shared a cache entry"


async def test_bad_tenant_header_is_rejected(client, seeded):
    for bad in ["0", "-3", "abc", ""]:
        response = await client.get("/api/care-notes", headers={"X-Tenant-ID": bad})
        assert response.status_code in (400, 422), f"accepted tenant header {bad!r}"


async def test_unknown_tenant_sees_an_empty_service(client, seeded):
    """A tenant with no rows sees nothing -- not an error, and not other data."""
    response = await client.get("/api/care-notes", headers=auth(9999))
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0
