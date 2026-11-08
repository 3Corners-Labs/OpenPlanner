"""The machine API in HTTP tests (ADR 0023): the app with the API on and a client of it.

``agent_api`` runs the app on a database with the demo data; ``Agent`` registers a machine
client as the superuser and trades its secret for a token, as a contact-center bot does.
"""

import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from fastapi import FastAPI

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.config import Settings
from app.domain.api_access import ApiAccessService
from app.domain.api_clients import ClientDraft, IssuedSecret
from app.domain.authorization import Role
from app.main import create_app
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.principals import principal

SUPER = principal("sub-super", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
"""The superuser who switches the API on and registers the clients."""
BASE = "/api/agent/v1"
"""Where the machine API is mounted."""
FORM = {"content-type": "application/x-www-form-urlencoded"}
"""The content type of a token request."""
OPEN_ROUTES = {"/oauth/token", "/openapi.json", "/docs"}
"""The routes of the API that need no token."""
TRANSFER = {"scopes": ["configuration:transfer"]}
"""The scope of a client that exports and imports the configuration package (ADR 0089)."""


@dataclass
class Agent:
    """The app with the machine API, its client and the service that manages API clients."""

    client: httpx.AsyncClient
    app: FastAPI
    service: ApiAccessService

    async def register(self, **overrides: object) -> IssuedSecret:
        """Register a client of organisation demo as the superuser; return it with its secret."""
        fields: dict[str, object] = {
            "name": "WxCC AI Agent",
            "organisation": "demo",
            "groups": [],
            "scopes": ["availability:read"],
            "networks": [],
        }
        fields.update(overrides)
        return await self.service.create_client(SUPER, ClientDraft.create(**fields), 180)  # type: ignore[arg-type]

    async def token(self, issued: IssuedSecret, **form: str) -> httpx.Response:
        """Ask a token for ``issued`` with HTTP Basic, with ``form`` added to the request."""
        basic = base64.b64encode(
            f"{quote(issued.client.client_id)}:{quote(issued.secret)}".encode()
        ).decode()
        return await self.client.post(
            f"{BASE}/oauth/token",
            content=httpx.QueryParams({"grant_type": "client_credentials", **form}).__str__(),
            headers={**FORM, "authorization": f"Basic {basic}"},
        )

    async def bearer(self, issued: IssuedSecret) -> dict[str, str]:
        """Return the authorization header with a fresh token for ``issued``."""
        response = await self.token(issued)
        assert response.status_code == 200, response.text
        return {"authorization": f"Bearer {response.json()['access_token']}"}


async def agent_api(settings: Settings, *, enabled: bool) -> AsyncIterator[Agent]:
    """Seed the demo data, run the app and yield it once; ``enabled`` also switches it on."""
    engine = create_engine(settings.database_url)
    await seed_demo(engine)
    await engine.dispose()
    app = create_app(settings, FixedClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        service: ApiAccessService = app.state.api_access
        if enabled:
            await service.save_settings(SUPER, enabled=True, docs_public=False)
        yield Agent(client, app, service)
