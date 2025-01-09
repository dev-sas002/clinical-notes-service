"""Care-note CRUD endpoints.

Every route here inherits the ``tenant_context`` dependency from the router, so
there is no route-level opportunity to forget the tenant.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.deps import (
    CareNoteServiceDep,
    FacilityIds,
    PageSize,
    tenant_context,
)
from app.schemas.care_note import (
    CareNoteCreate,
    CareNotePage,
    CareNoteRead,
    CareNoteUpdate,
)

router = APIRouter(
    prefix="/api/care-notes",
    tags=["care-notes"],
    dependencies=[Depends(tenant_context)],
)


@router.get("", response_model=CareNotePage, summary="List care notes")
async def list_care_notes(
    service: CareNoteServiceDep,
    page_size: PageSize,
    facility_ids: FacilityIds = None,
    page: Annotated[int, Query(ge=1)] = 1,
    patient_id: Annotated[str | None, Query(max_length=64)] = None,
) -> CareNotePage:
    """Return a page of the calling tenant's notes, newest first.

    ``page_size`` is clamped to the configured maximum, so a client cannot ask
    for the whole table.
    """
    return await service.list_page(
        page=page,
        page_size=page_size,
        facility_ids=facility_ids,
        patient_id=patient_id,
    )


@router.post(
    "",
    response_model=CareNoteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a care note",
)
async def create_care_note(
    payload: CareNoteCreate,
    service: CareNoteServiceDep,
) -> CareNoteRead:
    """Create a note. The tenant comes from the request context, not the body."""
    return await service.create(payload)


@router.get("/{note_id}", response_model=CareNoteRead, summary="Get a care note")
async def get_care_note(
    service: CareNoteServiceDep,
    note_id: Annotated[int, Path(ge=1)],
) -> CareNoteRead:
    """Fetch one note. Another tenant's note returns 404."""
    return await service.get(note_id)


@router.put("/{note_id}", response_model=CareNoteRead, summary="Update a care note")
async def update_care_note(
    payload: CareNoteUpdate,
    service: CareNoteServiceDep,
    note_id: Annotated[int, Path(ge=1)],
) -> CareNoteRead:
    """Partially update a note. Another tenant's note returns 404."""
    return await service.update(note_id, payload)


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a care note",
)
async def delete_care_note(
    service: CareNoteServiceDep,
    note_id: Annotated[int, Path(ge=1)],
) -> Response:
    """Delete a note. Another tenant's note returns 404."""
    await service.delete(note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
