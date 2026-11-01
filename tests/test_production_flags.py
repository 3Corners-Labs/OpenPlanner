"""The production app runs without debug and without interactive docs (ASVS V13.4.2).

Debug mode shows tracebacks with locals to the caller, and Swagger UI or ReDoc would load
scripts from a CDN and invite probing; neither may exist in the app that ``ENVIRONMENT=prod``
starts. The machine API publishes its own docs only when the superuser allows it (ADR 0023).
"""

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app

FRAMEWORK_DOC_PATHS = {"/docs", "/redoc", "/docs/oauth2-redirect"}


@pytest.fixture
def production(tmp_path: Path) -> Settings:
    return Settings(
        environment="prod",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'prod.db'}",
        worker_heartbeat_path=tmp_path / "worker.heartbeat",
        management_token_key=SecretStr("m" * 40),
        session_secret=SecretStr("s" * 40),
        public_base_url="https://afspraak.example.nl",
        oidc_discovery_url="https://idp.example.nl/.well-known/openid-configuration",
        oidc_client_secret=SecretStr("real-client-secret"),
    )


def test_the_production_app_has_debug_off(production: Settings) -> None:
    app = create_app(production)

    assert production.environment == "prod"
    assert app.debug is False


def test_the_production_app_has_no_interactive_docs(production: Settings) -> None:
    app = create_app(production)

    assert (app.docs_url, app.redoc_url) == (None, None)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not paths & FRAMEWORK_DOC_PATHS


@pytest.mark.parametrize("path", sorted(FRAMEWORK_DOC_PATHS | {"/api/agent/v1/docs"}))
async def test_the_production_app_answers_404_on_the_doc_paths(
    production: Settings, path: str
) -> None:
    app = create_app(production)
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="https://afspraak.example.nl") as client,
    ):
        response = await client.get(path)

    assert response.status_code == 404
    assert "swagger" not in response.text.lower()
    assert "redoc" not in response.text.lower()
