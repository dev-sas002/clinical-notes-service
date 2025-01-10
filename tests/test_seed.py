"""The seeder: synthetic, idempotent, and correctly scoped."""

from __future__ import annotations

import random

from sqlalchemy import func, select

from app.config import Settings
from app.core.tenancy import unscoped
from app.models.care_note import CATEGORIES, CareNote
from app.seed import _rows


def _settings() -> Settings:
    return Settings(
        seed_tenants=3,
        seed_facilities_per_tenant=4,
        seed_patients_per_facility=25,
        seed_days=30,
    )


def test_generated_rows_are_obviously_synthetic():
    """Nothing here may look like a real patient record."""
    from app.core.time_ranges import utcnow

    rows = _rows(200, now=utcnow(), rng=random.Random(1), settings=_settings())
    for row in rows:
        assert row["note_content"].startswith("SYNTHETIC FIXTURE")
        assert row["patient_id"].startswith("P-T")
        assert row["created_by"].startswith("staff_")


def test_generated_rows_respect_the_configured_bounds():
    from app.core.time_ranges import utcnow

    settings = _settings()
    rows = _rows(500, now=utcnow(), rng=random.Random(2), settings=settings)

    for row in rows:
        assert 1 <= row["tenant_id"] <= settings.seed_tenants
        assert 1 <= row["priority"] <= 5
        assert row["category"] in CATEGORIES
        # Facility ids encode their tenant, so a facility cannot straddle two.
        assert row["facility_id"] // 10 == row["tenant_id"]


def test_generation_is_deterministic_for_a_given_seed():
    from app.core.time_ranges import utcnow

    now = utcnow()
    first = _rows(50, now=now, rng=random.Random(7), settings=_settings())
    second = _rows(50, now=now, rng=random.Random(7), settings=_settings())
    assert first == second


def test_every_tenant_gets_data_at_a_realistic_volume():
    from app.core.time_ranges import utcnow

    settings = _settings()
    rows = _rows(1000, now=utcnow(), rng=random.Random(3), settings=settings)
    tenants = {row["tenant_id"] for row in rows}
    assert tenants == set(range(1, settings.seed_tenants + 1))


async def test_seeded_rows_are_visible_only_within_their_tenant(session):
    """End-to-end: insert generated rows, then read them back tenant by tenant."""
    from app.core.tenancy import tenant_scope
    from app.core.time_ranges import utcnow

    settings = _settings()
    rows = _rows(300, now=utcnow(), rng=random.Random(11), settings=settings)
    with unscoped():
        await session.execute(CareNote.__table__.insert(), rows)
        await session.commit()
        total = (await session.execute(select(func.count(CareNote.id)))).scalar_one()

    per_tenant = 0
    for tenant_id in range(1, settings.seed_tenants + 1):
        with tenant_scope(tenant_id):
            count = (await session.execute(select(func.count(CareNote.id)))).scalar_one()
        per_tenant += count

    assert total == 300
    assert per_tenant == total, "tenant counts must partition the table exactly"
