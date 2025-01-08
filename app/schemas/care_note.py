"""Request and response schemas for care notes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.care_note import PRIORITY_MAX, PRIORITY_MIN

Category = Literal["medication", "observation", "treatment"]
Priority = Annotated[int, Field(ge=PRIORITY_MIN, le=PRIORITY_MAX)]


class CareNoteBase(BaseModel):
    """Fields a client is allowed to supply.

    Note what is absent: ``tenant_id``. The tenant is taken from the
    authenticated request context, never from the body, so a client cannot
    write a note into somebody else's tenant by setting a field.
    """

    facility_id: int = Field(ge=1)
    patient_id: str = Field(min_length=1, max_length=64)
    category: Category
    priority: Priority
    created_by: str = Field(min_length=1, max_length=64)
    note_content: str = Field(min_length=1, max_length=10_000)
    created_at: datetime | None = Field(
        default=None, description="Defaults to the server's current UTC time."
    )


class CareNoteCreate(CareNoteBase):
    """Payload for creating a note."""


class CareNoteUpdate(BaseModel):
    """Payload for updating a note. Every field is optional (partial update)."""

    facility_id: int | None = Field(default=None, ge=1)
    patient_id: str | None = Field(default=None, min_length=1, max_length=64)
    category: Category | None = None
    priority: Priority | None = None
    created_by: str | None = Field(default=None, min_length=1, max_length=64)
    note_content: str | None = Field(default=None, min_length=1, max_length=10_000)


class CareNoteRead(BaseModel):
    """A note as returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    facility_id: int
    patient_id: str
    category: str
    priority: int
    created_at: datetime
    created_by: str
    note_content: str


class PageMeta(BaseModel):
    """Pagination envelope."""

    total: int
    page: int
    page_size: int
    total_pages: int


class CareNotePage(BaseModel):
    """A page of care notes."""

    notes: list[CareNoteRead]
    pagination: PageMeta
