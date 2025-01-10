"""Care-note CRUD, pagination and validation."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.tenancy import unscoped
from app.models.care_note import CareNote
from tests.conftest import TENANT_A, auth, make_note

VALID_NOTE = {
    "facility_id": 11,
    "patient_id": "P-T1-F11-500",
    "category": "observation",
    "priority": 3,
    "created_by": "staff_01",
    "note_content": "SYNTHETIC FIXTURE - valid note.",
}


# --- create -------------------------------------------------------------


async def test_create_returns_201_and_the_stored_note(client, seeded):
    response = await client.post("/api/care-notes", headers=auth(TENANT_A), json=VALID_NOTE)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["patient_id"] == VALID_NOTE["patient_id"]
    assert body["created_at"] is not None


async def test_create_defaults_created_at_to_now(client, seeded):
    payload = dict(VALID_NOTE)
    payload.pop("created_at", None)
    response = await client.post("/api/care-notes", headers=auth(TENANT_A), json=payload)
    assert response.status_code == 201
    assert response.json()["created_at"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("category", "gossip"),  # not one of the three categories
        ("priority", 0),  # below range
        ("priority", 6),  # above range
        ("priority", "high"),  # wrong type
        ("facility_id", -1),  # not a real id
        ("patient_id", ""),  # empty
        ("note_content", ""),  # empty
    ],
)
async def test_create_rejects_invalid_fields(client, seeded, field, value):
    """The original accepted a bare `dict` and passed it to the model.

    Anything unparseable became a 500; anything unexpected became a column.
    """
    payload = dict(VALID_NOTE) | {field: value}
    response = await client.post("/api/care-notes", headers=auth(TENANT_A), json=payload)
    assert response.status_code == 422, f"{field}={value!r} was accepted"


async def test_create_rejects_missing_required_fields(client, seeded):
    response = await client.post(
        "/api/care-notes", headers=auth(TENANT_A), json={"facility_id": 11}
    )
    assert response.status_code == 422


async def test_create_ignores_unknown_fields_instead_of_crashing(client, seeded):
    """`CareNote(**note)` used to raise TypeError -- a 500 -- on any extra key."""
    payload = dict(VALID_NOTE) | {"totally_unknown_column": "boom"}
    response = await client.post("/api/care-notes", headers=auth(TENANT_A), json=payload)
    assert response.status_code == 201


async def test_create_persists_the_row(client, session, seeded):
    await client.post("/api/care-notes", headers=auth(TENANT_A), json=VALID_NOTE)
    with unscoped():
        total = (await session.execute(select(func.count(CareNote.id)))).scalar_one()
    assert total == len(seeded["a"]) + len(seeded["b"]) + 1


# --- read ---------------------------------------------------------------


async def test_get_returns_the_note(client, seeded):
    note = seeded["a"][0]
    response = await client.get(f"/api/care-notes/{note.id}", headers=auth(TENANT_A))
    assert response.status_code == 200
    assert response.json()["id"] == note.id


async def test_get_missing_note_is_404(client, seeded):
    response = await client.get("/api/care-notes/999999", headers=auth(TENANT_A))
    assert response.status_code == 404


async def test_get_rejects_a_non_positive_id(client, seeded):
    assert (await client.get("/api/care-notes/0", headers=auth(TENANT_A))).status_code == 422


# --- update / delete ----------------------------------------------------


async def test_update_changes_only_the_supplied_fields(client, seeded):
    note = seeded["a"][0]
    original_patient = note.patient_id

    response = await client.put(
        f"/api/care-notes/{note.id}",
        headers=auth(TENANT_A),
        json={"priority": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["priority"] == 5
    assert body["patient_id"] == original_patient


async def test_update_validates_its_payload(client, seeded):
    note = seeded["a"][0]
    response = await client.put(
        f"/api/care-notes/{note.id}", headers=auth(TENANT_A), json={"priority": 99}
    )
    assert response.status_code == 422


async def test_delete_removes_the_note(client, seeded):
    note = seeded["a"][0]
    assert (
        await client.delete(f"/api/care-notes/{note.id}", headers=auth(TENANT_A))
    ).status_code == 204
    assert (
        await client.get(f"/api/care-notes/{note.id}", headers=auth(TENANT_A))
    ).status_code == 404


async def test_delete_missing_note_is_404(client, seeded):
    assert (
        await client.delete("/api/care-notes/999999", headers=auth(TENANT_A))
    ).status_code == 404


# --- listing and pagination ---------------------------------------------


async def test_list_is_ordered_newest_first(client, seeded):
    response = await client.get(
        "/api/care-notes", headers=auth(TENANT_A), params={"page_size": 100}
    )
    timestamps = [n["created_at"] for n in response.json()["notes"]]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_pagination_splits_the_result_set(client, seeded):
    first = (
        await client.get(
            "/api/care-notes", headers=auth(TENANT_A), params={"page": 1, "page_size": 2}
        )
    ).json()
    second = (
        await client.get(
            "/api/care-notes", headers=auth(TENANT_A), params={"page": 2, "page_size": 2}
        )
    ).json()

    assert len(first["notes"]) == 2
    assert first["pagination"]["total"] == len(seeded["a"])
    assert first["pagination"]["total_pages"] == 3
    assert {n["id"] for n in first["notes"]}.isdisjoint({n["id"] for n in second["notes"]})


async def test_page_beyond_the_end_is_empty_not_an_error(client, seeded):
    response = await client.get(
        "/api/care-notes", headers=auth(TENANT_A), params={"page": 99, "page_size": 20}
    )
    assert response.status_code == 200
    assert response.json()["notes"] == []


async def test_page_size_is_clamped_to_the_maximum(client, session, settings, seeded):
    """A client must not be able to request the entire table.

    The original endpoint honoured any `page_size`, so `?page_size=10000000`
    was a free full-table read.
    """
    with unscoped():
        session.add_all([make_note(TENANT_A, patient_suffix=i) for i in range(200)])
        await session.commit()

    response = await client.get(
        "/api/care-notes", headers=auth(TENANT_A), params={"page_size": 100000}
    )
    assert response.status_code == 200
    assert len(response.json()["notes"]) <= settings.max_page_size


async def test_invalid_page_is_rejected(client, seeded):
    assert (
        await client.get("/api/care-notes", headers=auth(TENANT_A), params={"page": 0})
    ).status_code == 422


async def test_malformed_facility_ids_is_a_400_not_a_500(client, seeded):
    """`int(id)` on unparsable input used to raise straight out of the handler."""
    response = await client.get(
        "/api/care-notes", headers=auth(TENANT_A), params={"facility_ids": "11,abc"}
    )
    assert response.status_code == 400


async def test_patient_filter_narrows_the_listing(client, seeded):
    target = seeded["a"][0].patient_id
    response = await client.get(
        "/api/care-notes", headers=auth(TENANT_A), params={"patient_id": target}
    )
    assert response.status_code == 200
    assert all(n["patient_id"] == target for n in response.json()["notes"])
