"""Data access for care notes.

Services never touch the session directly; they go through this repository.
Tenant filtering is applied automatically by the guard in
:mod:`app.core.tenancy`, so the queries here read as if the application were
single-tenant -- which is the point.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.care_note import CareNote


class CareNoteRepository:
    """All care-note persistence lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _apply_filters(
        stmt: Select,
        *,
        facility_ids: Sequence[int] | None = None,
        patient_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Select:
        """Apply the non-tenant filters shared by listing and analytics."""
        if facility_ids:
            stmt = stmt.where(CareNote.facility_id.in_(facility_ids))
        if patient_id is not None:
            stmt = stmt.where(CareNote.patient_id == patient_id)
        if start is not None:
            stmt = stmt.where(CareNote.created_at >= start)
        if end is not None:
            # Half-open: strictly less than `end`.
            stmt = stmt.where(CareNote.created_at < end)
        return stmt

    # -- reads -----------------------------------------------------------

    async def get(self, note_id: int) -> CareNote | None:
        """Fetch one note by id, scoped to the current tenant.

        A note belonging to another tenant comes back as ``None`` -- an
        indistinguishable 404 rather than a 403, so the endpoint does not leak
        the existence of other tenants' records.
        """
        stmt = select(CareNote).where(CareNote.id == note_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        facility_ids: Sequence[int] | None = None,
        patient_id: str | None = None,
    ) -> list[CareNote]:
        """Return one page of notes, newest first."""
        stmt = self._apply_filters(
            select(CareNote), facility_ids=facility_ids, patient_id=patient_id
        )
        stmt = stmt.order_by(CareNote.created_at.desc(), CareNote.id.desc())
        stmt = stmt.limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars().all())

    async def count(
        self,
        *,
        facility_ids: Sequence[int] | None = None,
        patient_id: str | None = None,
    ) -> int:
        """Count notes matching the filters."""
        stmt = self._apply_filters(
            select(func.count(CareNote.id)),
            facility_ids=facility_ids,
            patient_id=patient_id,
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def rollup(
        self,
        *,
        facility_ids: Sequence[int] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[tuple[int, str, int, int]]:
        """Group counts by ``(facility_id, category, priority)`` in one pass.

        The original implementation issued four separate aggregate queries over
        the same window. This is a single scan whose result set is bounded by
        ``facilities x categories x priorities`` -- small enough to fold in
        Python, no matter how many notes it summarises.
        """
        stmt = self._apply_filters(
            select(
                CareNote.facility_id,
                CareNote.category,
                CareNote.priority,
                func.count(CareNote.id),
            ),
            facility_ids=facility_ids,
            start=start,
            end=end,
        ).group_by(CareNote.facility_id, CareNote.category, CareNote.priority)
        rows = (await self._session.execute(stmt)).all()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    async def count_distinct_patients(
        self,
        *,
        facility_ids: Sequence[int] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        """Count distinct patients in the window.

        Cannot be folded into :meth:`rollup` -- a distinct count across groups
        is not derivable from per-group counts -- so it stays a second query.
        """
        stmt = self._apply_filters(
            select(func.count(func.distinct(CareNote.patient_id))),
            facility_ids=facility_ids,
            start=start,
            end=end,
        )
        return int((await self._session.execute(stmt)).scalar_one())

    # -- writes ----------------------------------------------------------

    async def add(self, note: CareNote) -> CareNote:
        """Persist a new note."""
        self._session.add(note)
        await self._session.flush()
        await self._session.refresh(note)
        return note

    async def delete(self, note_id: int) -> bool:
        """Delete a note by id. Returns whether a row was removed.

        The existence check runs through :meth:`get` so the tenant guard
        applies; the guard's automatic filtering covers SELECT only, which
        makes an unguarded bulk DELETE the one place a cross-tenant write could
        hide. Resolving the row first closes that gap.
        """
        existing = await self.get(note_id)
        if existing is None:
            return False
        await self._session.execute(
            delete(CareNote).where(CareNote.id == note_id, CareNote.tenant_id == existing.tenant_id)
        )
        return True
