"""The machine API over HTTP (ADR 0023): token endpoint, routes, errors, docs, switches."""

import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.api.agent.auth import RequireScope
from app.config import Settings
from app.domain.api_access import ApiAccessService
from app.domain.api_clients import ClientDraft, IssuedSecret
from app.domain.audit import Outcome
from app.domain.authorization import Principal, Role
from app.domain.services import BookingService
from app.main import create_app
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.routes import all_routes

SUPER = Principal("sub-super", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
BASE = "/api/agent/v1"
FORM = {"content-type": "application/x-www-form-urlencoded"}
OPEN_ROUTES = {"/oauth/token", "/openapi.json", "/docs"}


@dataclass
class Agent:
    client: httpx.AsyncClient
    app: FastAPI
    service: ApiAccessService

    async def register(self, **overrides: object) -> IssuedSecret:
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
        basic = base64.b64encode(
            f"{quote(issued.client.client_id)}:{quote(issued.secret)}".encode()
        ).decode()
        return await self.client.post(
            f"{BASE}/oauth/token",
            content=httpx.QueryParams({"grant_type": "client_credentials", **form}).__str__(),
            headers={**FORM, "authorization": f"Basic {basic}"},
        )

    async def bearer(self, issued: IssuedSecret) -> dict[str, str]:
        response = await self.token(issued)
        assert response.status_code == 200, response.text
        return {"authorization": f"Bearer {response.json()['access_token']}"}


async def _agent(settings: Settings, *, enabled: bool) -> AsyncIterator[Agent]:
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


@pytest.fixture
async def agent(settings: Settings, database_url: str) -> AsyncIterator[Agent]:
    on = settings.model_copy(update={"database_url": database_url, "agent_api_enabled": True})
    async for agent in _agent(on, enabled=True):
        yield agent


async def test_without_the_environment_switch_the_api_does_not_exist(
    settings: Settings, database_url: str
) -> None:
    off = settings.model_copy(update={"database_url": database_url})
    async for agent in _agent(off, enabled=True):
        response = await agent.client.post(f"{BASE}/oauth/token", content="", headers=FORM)
        assert response.status_code == 404


async def test_client_credentials_flow_end_to_end(agent: Agent) -> None:
    issued = await agent.register()

    response = await agent.token(issued)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    assert body["scope"] == "availability:read"
    assert body["access_token"].startswith("opa_")

    headers = {"authorization": f"Bearer {body['access_token']}"}
    me = await agent.client.get(f"{BASE}/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["client_id"] == issued.client.client_id
    assert me.json()["groups"] is None
    assert me.headers["cache-control"] == "no-store"

    groups = await agent.client.get(f"{BASE}/groups", headers=headers)
    assert [g["slug"] for g in groups.json()] == ["demo"]

    slots = await agent.client.get(
        f"{BASE}/groups/demo/availability",
        params={"from": "2026-01-05", "to": "2026-01-06", "lang": "en"},
        headers=headers,
    )
    assert slots.status_code == 200
    payload = slots.json()
    assert payload["timezone"] == "Europe/Amsterdam"
    assert payload["slots"], "the demo group is open on weekdays"
    first = payload["slots"][0]
    assert set(first) == {"start", "end", "local_date", "local_time", "label", "remaining"}
    assert "Monday" in first["label"]


async def test_form_credentials_work_too_but_not_both(agent: Agent) -> None:
    issued = await agent.register()
    form = {
        "grant_type": "client_credentials",
        "client_id": issued.client.client_id,
        "client_secret": issued.secret,
    }

    ok = await agent.client.post(
        f"{BASE}/oauth/token", content=str(httpx.QueryParams(form)), headers=FORM
    )
    both = await agent.token(issued, client_id=issued.client.client_id)

    assert ok.status_code == 200
    assert (both.status_code, both.json()) == (400, {"error": "invalid_request"})


@pytest.mark.parametrize(
    ("content", "headers", "status", "error"),
    [
        ("grant_type=password", FORM, 400, "unsupported_grant_type"),
        ("", FORM, 400, "invalid_request"),
        (
            "grant_type=client_credentials&grant_type=client_credentials",
            FORM,
            400,
            "invalid_request",
        ),
        (
            '{"grant_type": "client_credentials"}',
            {"content-type": "application/json"},
            400,
            "invalid_request",
        ),
        (
            "grant_type=client_credentials",
            {**FORM, "authorization": "Basic !!!"},
            401,
            "invalid_client",
        ),
        (
            "grant_type=client_credentials",
            {**FORM, "authorization": "Bearer x"},
            401,
            "invalid_client",
        ),
        ("grant_type=client_credentials&" + "x" * 5000, FORM, 400, "invalid_request"),
    ],
)
async def test_malformed_token_requests(
    agent: Agent, content: str, headers: dict[str, str], status: int, error: str
) -> None:
    response = await agent.client.post(f"{BASE}/oauth/token", content=content, headers=headers)

    assert (response.status_code, response.json()) == (status, {"error": error})


async def test_wrong_secret_is_invalid_client_and_audited_without_the_secret(
    agent: Agent,
) -> None:
    issued = await agent.register()
    wrong = IssuedSecret(issued.client, "ops_wrong-guess")

    response = await agent.token(wrong)

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_client"}
    assert response.headers["www-authenticate"].startswith("Basic")
    log = await agent.app.state.admin_service.audit_log(SUPER)
    event = log[0].event
    assert (event.action, event.outcome, event.reason) == (
        "api.token",
        Outcome.DENIED,
        "bad_secret",
    )
    assert "ops_wrong-guess" not in repr(log)


async def test_unknown_scope_is_invalid_scope(agent: Agent) -> None:
    issued = await agent.register()

    response = await agent.token(issued, scope="bookings:everything")

    assert (response.status_code, response.json()) == (400, {"error": "invalid_scope"})


@pytest.mark.parametrize(
    "authorization", [None, "Bearer", "Bearer opa_guessed", "Basic abc", "Bearer " + "a" * 300]
)
async def test_calls_without_a_valid_token_are_refused(
    agent: Agent, authorization: str | None
) -> None:
    headers = {"authorization": authorization} if authorization else {}

    response = await agent.client.get(f"{BASE}/me", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_token"}
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


async def test_another_organisations_group_is_insufficient_scope(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register())

    response = await agent.client.get(
        f"{BASE}/groups/andere-team/availability",
        params={"from": "2026-01-05", "to": "2026-01-06"},
        headers=headers,
    )
    missing = await agent.client.get(
        f"{BASE}/groups/nope/availability",
        params={"from": "2026-01-05", "to": "2026-01-06"},
        headers=headers,
    )

    assert (response.status_code, response.json()) == (403, {"error": "insufficient_scope"})
    assert (missing.status_code, missing.json()) == (404, {"error": "group_not_found"})


async def test_invalid_input_is_refused_without_echoing_it(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register())

    bad = await agent.client.get(
        f"{BASE}/groups/demo/availability",
        params={"from": "ada@example.org", "to": "2026-01-06"},
        headers=headers,
    )
    too_long = await agent.client.get(
        f"{BASE}/groups/demo/availability",
        params={"from": "2026-01-01", "to": "2026-12-31"},
        headers=headers,
    )

    assert (bad.status_code, bad.json()) == (422, {"error": "invalid_request"})
    assert "ada@example.org" not in bad.text
    assert (too_long.status_code, too_long.json()) == (422, {"error": "invalid_range"})


async def test_the_emergency_stop_refuses_tokens_and_calls(agent: Agent) -> None:
    issued = await agent.register()
    headers = await agent.bearer(issued)

    await agent.service.save_settings(SUPER, enabled=False, docs_public=False)

    call = await agent.client.get(f"{BASE}/me", headers=headers)
    token = await agent.token(issued)
    assert (call.status_code, call.json()) == (503, {"error": "temporarily_unavailable"})
    assert token.status_code == 503
    assert call.headers["retry-after"] == "300"


async def test_disabling_a_client_stops_its_token_at_once(agent: Agent) -> None:
    issued = await agent.register()
    headers = await agent.bearer(issued)

    await agent.service.set_enabled(SUPER, issued.client.client_id, False)

    assert (await agent.client.get(f"{BASE}/me", headers=headers)).status_code == 401


async def test_docs_exist_only_while_published(agent: Agent) -> None:
    hidden_spec = await agent.client.get(f"{BASE}/openapi.json")
    hidden_page = await agent.client.get(f"{BASE}/docs")
    assert (hidden_spec.status_code, hidden_page.status_code) == (404, 404)
    assert hidden_spec.json() == {"error": "not_found"}

    await agent.service.save_settings(SUPER, enabled=True, docs_public=True)

    spec = await agent.client.get(f"{BASE}/openapi.json")
    page = await agent.client.get(f"{BASE}/docs", params={"lang": "nl"})
    assert spec.status_code == 200
    paths = set(spec.json()["paths"])
    assert paths == {"/oauth/token", "/me", "/groups", "/groups/{slug}/availability"}
    assert spec.json()["servers"] == [{"url": BASE}]
    schemes = spec.json()["components"]["securitySchemes"]
    assert "clientCredentials" in schemes["clientCredentials"]["flows"]
    assert page.status_code == 200
    assert "/groups/{slug}/availability" in page.text
    assert "script" not in page.headers["content-security-policy"]


async def test_unknown_paths_answer_json(agent: Agent) -> None:
    response = await agent.client.get(f"{BASE}/bookings")

    assert (response.status_code, response.json()) == (404, {"error": "not_found"})


async def test_tokens_are_rate_limited_per_ip(settings: Settings, database_url: str) -> None:
    limited = settings.model_copy(
        update={
            "database_url": database_url,
            "agent_api_enabled": True,
            "rate_limit_enabled": True,
            "rate_limit_agent_tokens": "2/minute",
        }
    )
    async for agent in _agent(limited, enabled=True):
        wrong = IssuedSecret((await agent.register()).client, "ops_guess")
        statuses = [(await agent.token(wrong)).status_code for _ in range(3)]
        assert statuses == [401, 401, 429]


def test_every_route_declares_one_scope_except_token_and_docs(agent: Agent) -> None:
    mounted = next(r for r in agent.app.routes if getattr(r, "path", None) == BASE)
    routes = all_routes(mounted.app.routes)  # type: ignore[attr-defined]

    for route in routes:
        found = [d.call for d in route.dependant.dependencies if isinstance(d.call, RequireScope)]
        expected = 0 if route.path in OPEN_ROUTES else 1
        assert len(found) == expected, route.path
    assert OPEN_ROUTES <= {r.path for r in routes}


async def test_booking_service_is_shared(agent: Agent) -> None:
    # The API reads availability through the participants' service: same rules, same window.
    mounted = next(r for r in agent.app.routes if getattr(r, "path", None) == BASE)
    assert isinstance(mounted.app.state.booking_service, BookingService)  # type: ignore[attr-defined]
    assert mounted.app.state.booking_service is agent.app.state.booking_service  # type: ignore[attr-defined]
