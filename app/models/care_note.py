"""The CareNote model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenancy import TenantScoped
from app.db.base import Base

#: Categories a note may be filed under.
CATEGORIES = ("medication", "observation", "treatment")

#: Inclusive priority bounds.
PRIORITY_MIN = 1
PRIORITY_MAX = 5


class CareNote(Base, TenantScoped):
    """A single care note.

    Inheriting :class:`~app.core.tenancy.TenantScoped` is what enrols the table
    in automatic tenant filtering; it also supplies the ``tenant_id`` column.
    """

    __tablename__ = "care_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    facility_id: Mapped[int] = mapped_column(nullable=False)
    patient_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    note_content: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        # Every query is tenant-scoped, so tenant_id leads every index.
        # Serves the analytics rollup (tenant + time window, grouped by
        # facility) and facility-filtered listings.
        Index("ix_care_notes_tenant_facility_created", "tenant_id", "facility_id", "created_at"),
        # Serves the default listing: filter by tenant, order by created_at.
        Index("ix_care_notes_tenant_created", "tenant_id", "created_at"),
        # Serves per-patient history lookups.
        Index("ix_care_notes_tenant_patient_created", "tenant_id", "patient_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Deliberately excludes patient_id, created_by and note_content: this
        # object ends up in tracebacks and logs, and those fields are PHI.
        return f"<CareNote id={self.id} tenant={self.tenant_id} category={self.category!r}>"
