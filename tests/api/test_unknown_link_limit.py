"""Unknown management links are bounded per client address, whatever the path (ADR 0011, 0042).

slowapi counts per route and path, so every guessed ``/b/{token}`` path had its own bucket. The
bound counts only links that match no booking, in one bucket per address.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.domain.audit import AuditEvent, Category
from app.main import create_app
from tests.fakes.clock import FixedClock
from tests.support.admin import SUPER
from tests.support.citizen import SAME_ORIGIN, confirm_newest
from tests.support.domain import NOW

FRIDAY_0900 = "2026-01-02T08:00:00Z"
ADA = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.org", "phone": ""}
BOUND = 3
ROUTES = [
    ("GET", ""),
    ("GET", "/cancel"),
    ("POST", "/cancel"),
    ("GET", "/reschedule"),
    ("POST", "/reschedule"),
    ("GET", "/confirm"),
    ("POST", "/confirm"),
    ("GET", "/wait"),
    ("GET", "/wait/status"),
    ("GET", "/join"),
    ("GET", "/join/go"),
    ("GET", "/test"),
    ("POST", "/test"),
    ("GET", "/test/go"),
]


@dataclass
class Site:
    app: FastAPI

    def client(self, address: str = "203.0.113.7") -> httpx.AsyncClient:
        """A client whose requests come from ``address``."""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=(address, 4711)),
            base_url="http://testserver",
        )

    async def security_events(self) -> list[AuditEvent]:
        log = await self.app.state.admin_service.audit_log(SUPER)
        return [e.event for e in log if e.event.category is Category.SECURITY]


@asynccontextmanager
async def limited_site(settings: Settings, *, enabled: bool = True) -> AsyncIterator[Site]:
    """An app with the bound at ``BOUND`` per hour and the per-path limits out of the way."""
    limited = settings.model_copy(
        update={
            "rate_limit_enabled": enabled,
            "rate_limit_unknown_links": f"{BOUND}/hour",
            "rate_limit_reads": "1000/minute",
            "rate_limit_bookings": "1000/hour",
        }
    )
    app = create_app(limited, FixedClock(NOW))
    async with app.router.lifespan_context(app):
        yield Site(app)


async def hit(client: httpx.AsyncClient, method: str, path: str) -> httpx.Response:
    data = {"start": FRIDAY_0900} if method == "POST" else None
    return await client.request(method, path, data=data, headers=SAME_ORIGIN)


@pytest.mark.parametrize(("method", "suffix"), ROUTES)
async def test_different_unknown_tokens_from_one_address_hit_the_bound(
    demo_settings: Settings, method: str, suffix: str
) -> None:
    async with limited_site(demo_settings) as site, site.client() as client:
        statuses = [
            (await hit(client, method, f"/b/guess{n}{suffix}")).status_code
            for n in range(BOUND + 2)
        ]
        over = await hit(client, method, f"/b/guess-more{suffix}")

    assert statuses == [404] * BOUND + [429, 429]
    assert over.headers["retry-after"] == "60"
    assert "te vaak geprobeerd" in over.text


async def test_all_routes_share_one_bucket_per_address(demo_settings: Settings) -> None:
    async with limited_site(demo_settings) as site, site.client() as client:
        first = [await hit(client, "GET", "/b/a"), await hit(client, "POST", "/b/b/cancel")]
        third = await hit(client, "GET", "/b/c/wait/status")
        fourth = await hit(client, "GET", "/b/d/join")

    assert [r.status_code for r in first] == [404, 404]
    assert (third.status_code, fourth.status_code) == (404, 429)


async def test_valid_tokens_and_other_addresses_are_unaffected(demo_settings: Settings) -> None:
    async with limited_site(demo_settings) as site, site.client() as client:
        booked = await client.post(
            "/g/demo/book", data={"start": FRIDAY_0900, **ADA}, headers=SAME_ORIGIN
        )
        assert booked.status_code == 303
        token = await confirm_newest(client)
        for _ in range(BOUND + 2):  # valid links are not counted, however often they are read
            assert (await hit(client, "GET", f"/b/{token}")).status_code == 200
        for n in range(BOUND):
            assert (await hit(client, "GET", f"/b/guess{n}")).status_code == 404
        assert (await hit(client, "GET", "/b/guess-over")).status_code == 429

        own = await hit(client, "GET", f"/b/{token}")
        cancel_page = await hit(client, "GET", f"/b/{token}/cancel")
        async with site.client("198.51.100.9") as other:
            elsewhere = await hit(other, "GET", "/b/guess0")

    assert (own.status_code, cancel_page.status_code) == (200, 200)
    assert elsewhere.status_code == 404


async def test_the_off_switch_turns_the_bound_off(demo_settings: Settings) -> None:
    async with limited_site(demo_settings, enabled=False) as site, site.client() as client:
        statuses = [(await hit(client, "GET", f"/b/guess{n}")).status_code for n in range(8)]

    assert statuses == [404] * 8


async def test_a_flood_is_one_security_event_per_caller_and_window(demo_settings: Settings) -> None:
    async with limited_site(demo_settings) as site:
        async with site.client() as client:
            for n in range(BOUND + 5):
                await hit(client, "GET", f"/b/guess{n}")
        async with site.client("198.51.100.9") as other:
            for n in range(BOUND + 5):
                await hit(other, "GET", f"/b/guess{n}")
        events = await site.security_events()

    limited = [e for e in events if e.action == "request.rate_limited"]
    assert [(e.reason) for e in limited] == ["too_many_requests"] * 2
    assert all(e.detail.startswith("surface:links,client:") for e in limited)
    assert len({e.detail for e in limited}) == 2  # one per caller
    assert "203.0.113.7" not in repr(events)
    assert "guess" not in repr(events)
