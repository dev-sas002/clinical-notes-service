"""Shared fixtures.

All fixture data is obviously synthetic: patient ids look like ``P-T1-F11-001``
and every note body is prefixed ``SYNTHETIC FIXTURE``. Nothing here should ever
be mistaken for real patient data.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ANALYTICS_CACHE_TTL_SECONDS", "0")

from app.api.deps import get_session
from app.config import Settings
from app.core.tenancy import install_tenant_guard, unscoped
from app.db.base import Base
from app.main import create_app
from app.models.care_note import CareNote

#: Fixed "now" so date-range assertions are deterministic.
NOW = datetime(2026, 6, 15, 12, 0, 0)

TENANT_A = 1
TENANT_B = 2


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        analytics_cache_ttl_seconds=0,
        default_page_size=20,
        max_page_size=100,
    )


@pytest_asyncio.fixture
async def engine():
    """A fresh in-memory database per test.

    ``StaticPool`` keeps every connection pointed at the same in-memory
    database; without it each checkout would get its own empty one.
    """
    from sqlalchemy.pool import StaticPool

    install_tenant_guard()
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


def make_note(
    tenant_id: int,
    *,
    facility_id: int | None = None,
    patient_suffix: int = 1,
    category: str = "observation",
    priority: int = 3,
    created_at: datetime | None = None,
    content: str | None = None,
) -> CareNote:
    """Build one obviously-synthetic note."""
    facility_id = facility_id if facility_id is not None else tenant_id * 10 + 1
    patient_id = f"P-T{tenant_id}-F{facility_id}-{patient_suffix:03d}"
    return CareNote(
        tenant_id=tenant_id,
        facility_id=facility_id,
        patient_id=patient_id,
        category=category,
        priority=priority,
        created_at=created_at or NOW,
        created_by=f"staff_{tenant_id:02d}",
        note_content=content or f"SYNTHETIC FIXTURE - {category} note for {patient_id}.",
    )


@pytest_asyncio.fixture
async def seeded(session) -> dict[str, list[CareNote]]:
    """Two tenants with deliberately different, overlapping-looking data.

    Tenant A: 6 notes across facilities 11/12, spread over three days.
    Tenant B: 4 notes on facility 21, same days -- so a broken tenant filter
    shows up as wrong counts, not just as extra rows.
    """
    tenant_a = [
        make_note(
            TENANT_A,
            facility_id=11,
            patient_suffix=1,
            category="medication",
            priority=1,
            created_at=NOW,
        ),
        make_note(
            TENANT_A,
            facility_id=11,
            patient_suffix=1,
            category="medication",
            priority=2,
            created_at=NOW,
        ),
        make_note(
            TENANT_A,
            facility_id=11,
            patient_suffix=2,
            category="observation",
            priority=3,
            created_at=NOW,
        ),
        make_note(
            TENANT_A,
            facility_id=12,
            patient_suffix=3,
            category="treatment",
            priority=5,
            created_at=NOW,
        ),
        make_note(
            TENANT_A,
            facility_id=12,
            patient_suffix=4,
            category="observation",
            priority=3,
            created_at=NOW - timedelta(days=1),
        ),
        make_note(
            TENANT_A,
            facility_id=12,
            patient_suffix=5,
            category="observation",
            priority=4,
            created_at=NOW - timedelta(days=2),
        ),
    ]
    tenant_b = [
        make_note(
            TENANT_B,
            facility_id=21,
            patient_suffix=1,
            category="medication",
            priority=1,
            created_at=NOW,
        ),
        make_note(
            TENANT_B,
            facility_id=21,
            patient_suffix=2,
            category="medication",
            priority=1,
            created_at=NOW,
        ),
        make_note(
            TENANT_B,
            facility_id=21,
            patient_suffix=3,
            category="treatment",
            priority=2,
            created_at=NOW,
        ),
        make_note(
            TENANT_B,
            facility_id=21,
            patient_suffix=4,
            category="treatment",
            priority=2,
            created_at=NOW - timedelta(days=1),
        ),
    ]
    with unscoped():
        session.add_all(tenant_a + tenant_b)
        await session.commit()
    return {"a": tenant_a, "b": tenant_b}


@pytest_asyncio.fixture
async def client(session, settings) -> AsyncIterator[AsyncClient]:
    """An HTTP client wired to the app, sharing the test's session."""
    app = create_app(settings)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_session
    app.state.analytics_cache = None

    # raise_app_exceptions=False so the registered 500 handler's response is
    # observable; Starlette re-raises after handling otherwise.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def auth(tenant_id: int) -> dict[str, str]:
    """Headers identifying the calling tenant."""
    return {"X-Tenant-ID": str(tenant_id)}
