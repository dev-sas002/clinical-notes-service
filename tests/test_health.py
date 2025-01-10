"""Health endpoints and the OpenAPI schema."""

from __future__ import annotations


async def test_health_needs_no_tenant_header(client):
    """Probes are for the load balancer, so they must not require a tenant."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_ready_checks_the_database(client):
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


async def test_openapi_schema_is_served(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Care Notes API"
    for path in ["/api/care-notes", "/api/care-notes/{note_id}", "/api/care-stats", "/health"]:
        assert path in schema["paths"], f"{path} missing from the OpenAPI schema"


async def test_create_schema_does_not_expose_tenant_id(client):
    """The documented contract must not offer `tenant_id` as an input."""
    schema = (await client.get("/openapi.json")).json()
    properties = schema["components"]["schemas"]["CareNoteCreate"]["properties"]
    assert "tenant_id" not in properties
