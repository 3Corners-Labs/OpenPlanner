from pathlib import Path

import httpx

from app.config import Settings
from tests.conftest import app_client


async def test_liveness_is_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_checks_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_unavailable_without_leaking_details(
    unreachable_db_settings: Settings, tmp_path: Path
) -> None:
    async with app_client(unreachable_db_settings) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert str(tmp_path) not in response.text


async def test_security_headers_on_every_response(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "max-age=" in response.headers["strict-transport-security"]
    assert "camera=()" in response.headers["permissions-policy"]
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert response.headers["cross-origin-embedder-policy"] == "require-corp"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"


async def test_interactive_docs_are_disabled(client: httpx.AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 404
    assert (await client.get("/redoc")).status_code == 404


async def test_openapi_spec_is_published(client: httpx.AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert "/healthz" in response.json()["paths"]
