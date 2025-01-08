"""Seed the database with synthetic care notes.

Run explicitly -- ``python -m app.seed`` -- never from application startup.

The data is obviously fake on purpose. Patient identifiers are of the form
``P-T1-F11-007`` and note bodies say so in as many words: this is a healthcare
service, and seeding it with anything resembling real clinical text would be a
liability the moment somebody copied the fixture into a real environment.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.config import get_settings
from app.core.logging import configure_logging
from app.core.tenancy import install_tenant_guard, unscoped
from app.core.time_ranges import utcnow
from app.db.session import create_schema, dispose_engine, session_scope
from app.models.care_note import CATEGORIES, CareNote

logger = logging.getLogger(__name__)

NOTE_TEMPLATES = (
    "SYNTHETIC FIXTURE - routine {category} entry recorded for {patient}.",
    "SYNTHETIC FIXTURE - {category} check completed, no action required for {patient}.",
    "SYNTHETIC FIXTURE - scheduled {category} review logged against {patient}.",
    "SYNTHETIC FIXTURE - follow-up {category} note for {patient}, sample data only.",
)


def _rows(count: int, *, now: datetime, rng: random.Random, settings) -> list[dict]:
    rows: list[dict] = []
    for _ in range(count):
        tenant_id = rng.randint(1, settings.seed_tenants)
        facility_id = tenant_id * 10 + rng.randint(1, settings.seed_facilities_per_tenant)
        patient_no = rng.randint(1, settings.seed_patients_per_facility)
        patient_id = f"P-T{tenant_id}-F{facility_id}-{patient_no:03d}"
        category = rng.choice(CATEGORIES)
        # Skew towards recent notes, as a real ward would.
        days_ago = rng.triangular(0, settings.seed_days, settings.seed_days / 6)
        created_at = now - timedelta(
            days=days_ago, hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
        )
        rows.append(
            {
                "tenant_id": tenant_id,
                "facility_id": facility_id,
                "patient_id": patient_id,
                "category": category,
                "priority": rng.randint(1, 5),
                "created_at": created_at,
                "created_by": f"staff_{rng.randint(1, 20):02d}",
                "note_content": rng.choice(NOTE_TEMPLATES).format(
                    category=category, patient=patient_id
                ),
            }
        )
    return rows


async def seed(count: int | None = None, *, force: bool = False, seed_value: int = 20260923) -> int:
    """Insert ``count`` synthetic notes. Returns the number inserted.

    Idempotent by default: if the table already has rows it does nothing, so
    ``docker compose up`` twice does not double the dataset.
    """
    settings = get_settings()
    count = settings.seed_notes if count is None else count
    install_tenant_guard()
    await create_schema()

    rng = random.Random(seed_value)
    now = utcnow()

    async with session_scope() as session:
        # Seeding is legitimately cross-tenant, so it says so.
        with unscoped():
            existing = (await session.execute(select(func.count(CareNote.id)))).scalar_one()
            if existing and not force:
                logger.info("Database already holds %s notes; skipping seed.", existing)
                return 0

            inserted = 0
            batch = settings.seed_batch_size
            while inserted < count:
                chunk = min(batch, count - inserted)
                await session.execute(
                    CareNote.__table__.insert(),
                    _rows(chunk, now=now, rng=rng, settings=settings),
                )
                await session.commit()
                inserted += chunk
                logger.info("Seeded %s/%s notes", inserted, count)

    logger.info(
        "Seeding complete: %s synthetic notes across %s tenants.",
        count,
        settings.seed_tenants,
    )
    return count


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic care notes.")
    parser.add_argument("--count", type=int, default=None, help="Number of notes to insert.")
    parser.add_argument("--force", action="store_true", help="Seed even if rows exist.")
    args = parser.parse_args()

    configure_logging(get_settings().log_level)
    try:
        await seed(args.count, force=args.force)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
