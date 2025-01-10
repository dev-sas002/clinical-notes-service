"""PHI must not escape into logs or error payloads.

This is healthcare-adjacent data. The original code printed whole result sets
and whole stats dicts on every request, and returned `str(exception)` in 500
bodies -- both of which put patient identifiers and note text where they do not
belong.
"""

from __future__ import annotations

import logging

from app.core.logging import SENSITIVE_FIELDS, scrub
from app.models.care_note import CareNote
from tests.conftest import TENANT_A, auth

SECRET_CONTENT = "SYNTHETIC FIXTURE - highly-distinctive-note-body-marker"
SECRET_PATIENT = "P-T1-F11-distinctive-patient-marker"


def test_scrub_redacts_every_sensitive_field():
    payload = {
        "id": 1,
        "tenant_id": 1,
        "patient_id": SECRET_PATIENT,
        "note_content": SECRET_CONTENT,
        "created_by": "staff_01",
    }
    cleaned = scrub(payload)
    assert cleaned["id"] == 1
    assert cleaned["tenant_id"] == 1
    for field in SENSITIVE_FIELDS:
        assert cleaned[field] == "[redacted]"


def test_model_repr_excludes_phi():
    """The repr lands in tracebacks and log records, so it must be clean."""
    note = CareNote(
        id=1,
        tenant_id=1,
        facility_id=11,
        patient_id=SECRET_PATIENT,
        category="observation",
        priority=3,
        created_by="staff_01",
        note_content=SECRET_CONTENT,
    )
    rendered = repr(note)
    assert SECRET_PATIENT not in rendered
    assert SECRET_CONTENT not in rendered
    assert "staff_01" not in rendered
    assert "tenant=1" in rendered


async def test_requests_do_not_log_patient_data(client, session, seeded, caplog):
    """Exercise the main read paths and assert the logs stay clean."""
    from app.core.tenancy import unscoped
    from tests.conftest import make_note

    with unscoped():
        session.add_all([make_note(TENANT_A, content=SECRET_CONTENT)])
        await session.commit()

    with caplog.at_level(logging.DEBUG):
        await client.get("/api/care-notes", headers=auth(TENANT_A), params={"page_size": 100})
        await client.get("/api/care-stats", headers=auth(TENANT_A), params={"range": "all_time"})

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert SECRET_CONTENT not in logged
    assert "P-T1-F11" not in logged


async def test_internal_errors_do_not_leak_details_to_the_client(client, seeded, monkeypatch):
    """A 500 body must be generic; the original returned `str(exception)`."""
    from app.services.care_notes import CareNoteService

    async def _explode(self, **kwargs):
        raise RuntimeError(f"database exploded while reading {SECRET_PATIENT}")

    monkeypatch.setattr(CareNoteService, "list_page", _explode)

    response = await client.get("/api/care-notes", headers=auth(TENANT_A), params={"page_size": 10})
    assert response.status_code == 500
    assert SECRET_PATIENT not in response.text
    assert "exploded" not in response.text
    assert response.json()["detail"] == "Internal server error."
