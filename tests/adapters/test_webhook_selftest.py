"""Webhook delivery self-test: signed test events reach OpenPlanner and touch nothing."""

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.diagnostics import passed
from app.adapters.webhook_selftest import (
    Delivery,
    Outcome,
    deliveries_from_report,
    deliveries_to_report,
    delivery_checks,
    self_test,
)
from app.config import Settings
from app.main import create_app
from tests.fakes.clock import FixedClock
from tests.support.database import migrated_database
from tests.support.domain import NOW

SECRET = "s" * 32  # test value
BASE = "https://openplanner.example.nl"


@pytest.fixture
def configured(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    return settings.model_copy(
        update={
            "database_url": migrated_database(tmp_path, monkeypatch, "selftest"),
            "webex_webhook_secret": SecretStr(SECRET),
            "wxcc_webhook_secret": SecretStr(SECRET),
        }
    )


async def _against_app(
    settings: Settings, webex_secret: str | None, wxcc_secret: str | None
) -> list[Delivery]:
    """Run the self-test against the real application in-process."""
    app = create_app(settings, FixedClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http,
    ):
        return await self_test(
            http, BASE, webex_secret=webex_secret, wxcc_secret=wxcc_secret, now=NOW
        )


async def test_signed_events_are_accepted_and_unsigned_ones_refused(configured: Settings) -> None:
    results = await _against_app(configured, SECRET, SECRET)

    assert [(r.endpoint, r.signed, r.status, r.outcome) for r in results] == [
        ("webex", True, 204, Outcome.DELIVERED),
        ("webex", False, 401, Outcome.REFUSED),
        ("wxcc", True, 204, Outcome.DELIVERED),
        ("wxcc", False, 401, Outcome.REFUSED),
    ]
    assert passed(delivery_checks(results))


async def test_a_different_secret_in_web_is_named(configured: Settings) -> None:
    results = await _against_app(configured, "other", None)

    assert results[0].outcome is Outcome.SIGNATURE_REFUSED
    assert [r.endpoint for r in results] == ["webex", "webex"]
    checks = delivery_checks(results)
    assert not passed(checks)
    assert "differs from the web container's" in checks[0].hint


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (404, Outcome.NOT_FOUND),
        (429, Outcome.RATE_LIMITED),
        (530, Outcome.PROXY_ERROR),
        (302, Outcome.UNEXPECTED),
    ],
)
async def test_what_a_status_means_is_reported(status: int, outcome: Outcome) -> None:
    def route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
        results = await self_test(http, BASE, webex_secret=SECRET, wxcc_secret=None, now=NOW)

    assert results[0].outcome is outcome
    assert results[0].status == status


async def test_an_unreachable_base_url_is_reported_without_a_status() -> None:
    def route(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
        results = await self_test(http, BASE, webex_secret=None, wxcc_secret=SECRET, now=NOW)

    assert {(r.endpoint, r.status, r.outcome) for r in results} == {
        ("wxcc", None, Outcome.UNREACHABLE)
    }
    assert "WEBHOOK_BASE_URL (ConnectError)" in delivery_checks(results)[0].hint


async def test_an_endpoint_that_accepts_unsigned_events_fails() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(204))
    ) as http:
        results = await self_test(http, BASE, webex_secret=SECRET, wxcc_secret=None, now=NOW)

    assert results[1].outcome is Outcome.UNSIGNED_ACCEPTED
    assert not passed(delivery_checks(results))


async def test_the_test_events_carry_no_bearer_token_and_unknown_ids() -> None:
    seen: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
        await self_test(http, BASE, webex_secret=SECRET, wxcc_secret=SECRET, now=NOW)

    assert {str(r.url) for r in seen} == {f"{BASE}/webhooks/webex", f"{BASE}/webhooks/wxcc"}
    assert all("authorization" not in r.headers for r in seen)
    assert all(b"openplanner-selftest-" in r.content for r in seen)
    assert all(SECRET.encode() not in r.content for r in seen)


async def test_results_round_trip_through_the_stored_report() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(401))
    ) as http:
        results = await self_test(http, BASE, webex_secret=SECRET, wxcc_secret=SECRET, now=NOW)

    assert deliveries_from_report(deliveries_to_report(results)) == results
