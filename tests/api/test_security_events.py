"""Attempts on public entry points become security events with a caller pseudonym (ADR 0053)."""

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.domain.audit import AuditEvent, Category, Outcome
from app.main import create_app
from tests.fakes.clock import FixedClock
from tests.support.admin import SUPER
from tests.support.domain import NOW

FRIDAY_0900 = "2026-01-02T08:00:00Z"  # 09:00 Amsterdam, a free slot of the demo group
ADA = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.org", "phone": ""}
SAME_ORIGIN = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
CLIENT = re.compile(r"client:[0-9a-f]{16}")


@dataclass
class Site:
    client: httpx.AsyncClient
    app: FastAPI

    async def security_events(self) -> list[AuditEvent]:
        log = await self.app.state.admin_service.audit_log(SUPER)
        return [e.event for e in log if e.event.category is Category.SECURITY]


async def _site(settings: Settings) -> AsyncIterator[Site]:
    app = create_app(settings, FixedClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 4711)),
            base_url="http://testserver",
        ) as client,
    ):
        yield Site(client, app)


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[Site]:
    async for site in _site(demo_settings):
        yield site


def _only(events: list[AuditEvent]) -> AuditEvent:
    assert len(events) == 1, events
    return events[0]


async def test_a_filled_in_honeypot_is_a_security_event_without_the_address(site: Site) -> None:
    await site.client.post(
        "/g/demo/book",
        data={"start": FRIDAY_0900, **ADA, "website": "https://spam.example"},
        headers=SAME_ORIGIN,
    )

    event = _only(await site.security_events())
    assert (event.action, event.outcome, event.reason) == (
        "request.rejected",
        Outcome.DENIED,
        "honeypot",
    )
    assert CLIENT.fullmatch(event.detail)
    assert "203.0.113.7" not in repr(event)
    assert "spam.example" not in repr(event)


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({"sec-fetch-site": "cross-site"}, "cross_site"),
        ({"origin": "https://evil.example"}, "origin_mismatch"),
    ],
)
async def test_a_cross_site_post_is_a_security_event(
    site: Site, headers: dict[str, str], reason: str
) -> None:
    await site.client.post("/g/demo/book", data={"start": FRIDAY_0900, **ADA}, headers=headers)

    event = _only(await site.security_events())
    assert (event.action, event.reason) == ("request.rejected", reason)
    assert "evil.example" not in repr(event)


async def test_ordinary_bad_input_is_not_a_security_event(site: Site) -> None:
    await site.client.post("/g/demo/book", data={"start": "garbage", **ADA}, headers=SAME_ORIGIN)

    assert await site.security_events() == []


async def test_an_unknown_management_link_is_a_security_event(site: Site) -> None:
    response = await site.client.get("/b/guessed-token")

    assert response.status_code == 404
    event = _only(await site.security_events())
    assert (event.action, event.reason) == ("token.rejected", "unknown_management_link")
    assert "guessed-token" not in repr(event)


async def test_an_unknown_group_is_not_a_security_event(site: Site) -> None:
    await site.client.get("/g/nope")

    assert await site.security_events() == []


async def test_a_rate_limit_flood_is_one_event_per_caller(demo_settings: Settings) -> None:
    limited = demo_settings.model_copy(
        update={"rate_limit_enabled": True, "rate_limit_reads": "1/minute"}
    )
    async for site in _site(limited):
        statuses = [(await site.client.get("/g/demo")).status_code for _ in range(5)]

        assert statuses == [200, 429, 429, 429, 429]
        event = _only(await site.security_events())
        assert (event.action, event.reason) == ("request.rate_limited", "too_many_requests")
        assert event.detail.startswith("surface:web,client:")


async def test_an_unknown_bearer_token_on_the_machine_api_is_a_security_event(
    demo_settings: Settings,
) -> None:
    on = demo_settings.model_copy(update={"agent_api_enabled": True})
    async for site in _site(on):
        await site.app.state.api_access.save_settings(SUPER, enabled=True, docs_public=False)

        response = await site.client.get(
            "/api/agent/v1/me", headers={"authorization": "Bearer opa_guessed"}
        )

        assert response.status_code == 401
        event = _only(await site.security_events())
        assert (event.action, event.reason) == ("token.rejected", "unknown_token")
        assert "opa_guessed" not in repr(event)
