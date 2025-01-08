"""Care-note business logic."""

from __future__ import annotations

from collections.abc import Sequence

from app.core.tenancy import current_tenant_id
from app.core.time_ranges import utcnow
from app.models.care_note import CareNote
from app.repositories.care_notes import CareNoteRepository
from app.schemas.care_note import (
    CareNoteCreate,
    CareNotePage,
    CareNoteRead,
    CareNoteUpdate,
    PageMeta,
)


class CareNoteNotFoundError(LookupError):
    """Raised when a note does not exist *for the current tenant*."""


class CareNoteService:
    """Create, read, update and delete notes for the current tenant."""

    def __init__(self, repository: CareNoteRepository) -> None:
        self._repo = repository

    async def get(self, note_id: int) -> CareNoteRead:
        note = await self._repo.get(note_id)
        if note is None:
            raise CareNoteNotFoundError(note_id)
        return CareNoteRead.model_validate(note)

    async def list_page(
        self,
        *,
        page: int,
        page_size: int,
        facility_ids: Sequence[int] | None = None,
        patient_id: str | None = None,
    ) -> CareNotePage:
        offset = (page - 1) * page_size
        notes = await self._repo.list_page(
            limit=page_size,
            offset=offset,
            facility_ids=facility_ids,
            patient_id=patient_id,
        )
        total = await self._repo.count(facility_ids=facility_ids, patient_id=patient_id)
        total_pages = (total + page_size - 1) // page_size if total else 0
        return CareNotePage(
            notes=[CareNoteRead.model_validate(n) for n in notes],
            pagination=PageMeta(
                total=total, page=page, page_size=page_size, total_pages=total_pages
            ),
        )

    async def create(self, payload: CareNoteCreate) -> CareNoteRead:
        """Create a note for the current tenant.

        ``tenant_id`` comes from the request context, not the payload -- the
        schema has no such field, so there is nothing for a caller to forge.
        """
        note = CareNote(
            tenant_id=current_tenant_id(),
            facility_id=payload.facility_id,
            patient_id=payload.patient_id,
            category=payload.category,
            priority=payload.priority,
            created_at=payload.created_at or utcnow(),
            created_by=payload.created_by,
            note_content=payload.note_content,
        )
        return CareNoteRead.model_validate(await self._repo.add(note))

    async def update(self, note_id: int, payload: CareNoteUpdate) -> CareNoteRead:
        note = await self._repo.get(note_id)
        if note is None:
            raise CareNoteNotFoundError(note_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(note, field, value)
        return CareNoteRead.model_validate(await self._repo.add(note))

    async def delete(self, note_id: int) -> None:
        if not await self._repo.delete(note_id):
            raise CareNoteNotFoundError(note_id)
