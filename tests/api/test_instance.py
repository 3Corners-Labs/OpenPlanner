"""The instance endpoint, its abuse cases, and the watch on forwarded headers (ADR 0086)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI

from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.audit import AuditEvent, Category
from app.domain.public_address import EVENT, nonce_hash
from app.main import create_app
from tests.fakes.clock import FixedClock
from tests.support.admin import SUPER
from tests.support.domain import NOW

PROXY = "172.18.0.1"
VISITOR = "8.8.4.4"
"""A public address standing for a visitor: it must appear nowhere."""


@dataclass
class Site:
    client: httpx.AsyncClient
    app: FastAPI

    async def store_nonce(self, nonce: str) -> None:
        async with SqlUnitOfWork(self.app.state.engine).locked() as tx:
            await tx.store_probe_nonce(nonce_hash(nonce), NOW)

    async def events(self) -> list[AuditEvent]:
        log = await self.app.state.admin_service.audit_log(SUPER)
        return [e.event for e in log if e.event.action == EVENT]


async def open_site(settings: Settings, peer: str) -> AsyncIterator[Site]:
    app = create_app(settings, FixedClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(peer, 4711)),
            base_url="http://testserver",
        ) as client,
    ):
        yield Site(client, app)


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[Site]:
    async for site in open_site(demo_settings, PROXY):
        yield site


async def test_a_stranger_gets_the_installation_id_and_nothing_else(site: Site) -> None:
    first = await site.client.get("/api/v1/instance")
    second = await site.client.get("/api/v1/instance")

    assert first.status_code == 200
    assert set(first.json()) == {"instance_id", "scheme", "forwarded"}
    assert first.json()["scheme"] is None
    assert first.json()["forwarded"] is None
    assert first.json()["instance_id"] == second.json()["instance_id"]
    assert len(first.json()["instance_id"]) == 36


async def test_the_answer_is_not_cached_sets_no_cookie_and_holds_no_address(site: Site) -> None:
    await site.store_nonce("n1")

    response = await site.client.get(
        "/api/v1/instance?probe=n1", headers={"x-forwarded-for": VISITOR}
    )

    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    for address in (PROXY, VISITOR, "testserver"):
        assert address not in response.text


async def test_the_holder_of_the_nonce_gets_the_scheme_and_what_the_app_did_with_the_headers(
    site: Site,
) -> None:
    await site.store_nonce("n1")

    response = await site.client.get("/api/v1/instance?probe=n1")

    body = response.json()
    assert (body["scheme"], body["forwarded"]) == ("http", "absent")


async def test_forwarded_headers_from_a_peer_that_is_not_trusted_are_ignored_and_say_so(
    site: Site,
) -> None:
    # The app runs without uvicorn's proxy middleware here: like a proxy missing from
    # FORWARDED_ALLOW_IPS, the peer's own address and scheme stay.
    await site.store_nonce("n1")

    response = await site.client.get(
        "/api/v1/instance?probe=n1",
        headers={"x-forwarded-for": VISITOR, "x-forwarded-proto": "https"},
    )

    body = response.json()
    assert (body["scheme"], body["forwarded"]) == ("http", "ignored")


async def test_forwarded_headers_that_the_app_took_over_are_honoured(
    demo_settings: Settings,
) -> None:
    async for site in open_site(demo_settings, VISITOR):
        await site.store_nonce("n1")
        # uvicorn took the visitor's address from the header: the client is a hop of it.
        response = await site.client.get(
            "/api/v1/instance?probe=n1", headers={"x-forwarded-for": VISITOR}
        )

        assert response.json()["forwarded"] == "honoured"


async def test_abuse_a_guessed_nonce_gets_no_detail_and_does_not_burn_the_pending_one(
    site: Site,
) -> None:
    await site.store_nonce("n1")

    guess = await site.client.get("/api/v1/instance?probe=n0")
    real = await site.client.get("/api/v1/instance?probe=n1")

    assert guess.json()["scheme"] is None
    assert real.json()["scheme"] == "http"


async def test_abuse_a_nonce_works_once_only(site: Site) -> None:
    await site.store_nonce("n1")

    first = await site.client.get("/api/v1/instance?probe=n1")
    replay = await site.client.get("/api/v1/instance?probe=n1")

    assert first.json()["scheme"] == "http"
    assert replay.json()["scheme"] is None


async def test_abuse_an_oversized_probe_value_is_refused_before_it_reaches_the_database(
    site: Site,
) -> None:
    response = await site.client.get("/api/v1/instance?probe=" + "a" * 65)

    assert response.status_code == 422


async def test_abuse_the_endpoint_falls_under_the_availability_read_limit(
    demo_settings: Settings,
) -> None:
    limited = demo_settings.model_copy(
        update={"rate_limit_enabled": True, "rate_limit_reads": "2/minute"}
    )
    async for site in open_site(limited, PROXY):
        codes = [(await site.client.get("/api/v1/instance")).status_code for _ in range(3)]

    assert codes == [200, 200, 429]


async def test_the_endpoint_is_in_the_openapi_spec_without_a_permission(site: Site) -> None:
    spec = (await site.client.get("/openapi.json")).json()

    assert "get" in spec["paths"]["/api/v1/instance"]


async def test_forwarded_headers_that_are_ignored_leave_one_system_event_with_codes_only(
    site: Site,
) -> None:
    headers = {"x-forwarded-for": VISITOR, "x-forwarded-proto": "https"}
    for _ in range(3):
        await site.client.get("/healthz", headers=headers)

    events = await site.events()
    watch = site.app.state.proxy_watch.sighting()

    assert [(e.reason, e.detail, e.category) for e in events] == [
        ("proxy_headers_untrusted", "peer:private", Category.SYSTEM)
    ]
    assert watch is not None
    assert (watch.count, watch.peer) == (3, PROXY)
    assert VISITOR not in repr(events)
    assert VISITOR not in repr(watch)


async def test_a_public_peer_is_counted_without_its_address(demo_settings: Settings) -> None:
    async for site in open_site(demo_settings, VISITOR):
        await site.client.get("/healthz", headers={"x-forwarded-proto": "https"})

        watch = site.app.state.proxy_watch.sighting()
        assert watch is not None
        assert watch.peer is None
        assert [e.detail for e in await site.events()] == ["peer:public"]
        assert VISITOR not in repr(await site.events())


async def test_requests_without_forwarded_headers_or_with_honoured_ones_are_not_counted(
    demo_settings: Settings,
) -> None:
    async for site in open_site(demo_settings, VISITOR):
        await site.client.get("/healthz")
        await site.client.get("/healthz", headers={"x-forwarded-for": VISITOR})

        assert site.app.state.proxy_watch.sighting() is None
        assert await site.events() == []


async def test_an_event_that_cannot_be_written_never_fails_the_request(site: Site) -> None:
    class Broken:
        async def record(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("database is gone")

    site.app.state.system_log = Broken()

    response = await site.client.get("/healthz", headers={"x-forwarded-proto": "https"})

    assert response.status_code == 200
