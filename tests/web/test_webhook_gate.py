"""The webhook gate's parts that need no database: the limit's buckets and the route sweep."""

from collections.abc import Iterator

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute

from app.config import Settings
from app.main import create_app
from app.web.protection import configure_rate_limits, within_webhook_limit
from app.web.route_dependencies import all_routes, guards
from app.web.webhook_gate import WebhookGate


@pytest.fixture(autouse=True)
def _restore_limits() -> Iterator[None]:
    """Leave the process-wide limiter as the other tests expect it."""
    yield
    configure_rate_limits(enabled=False, bookings="10/hour", reads="60/minute")


def test_a_bucket_is_one_platform_one_organisation_and_one_address() -> None:
    configure_rate_limits(enabled=True, bookings="10/hour", reads="60/minute", webhooks="2/minute")

    assert within_webhook_limit("wxcc", "acme", "203.0.113.7")
    assert within_webhook_limit("wxcc", "acme", "203.0.113.7")
    assert not within_webhook_limit("wxcc", "acme", "203.0.113.7")
    # Two organisations behind one caller address do not share a bucket (ADR 0124 point 5).
    assert within_webhook_limit("wxcc", "voorbeeld", "203.0.113.7")
    # Nor do two platforms, two callers, or the installation's own address.
    assert within_webhook_limit("webex", "acme", "203.0.113.7")
    assert within_webhook_limit("wxcc", "acme", "203.0.113.8")
    assert within_webhook_limit("wxcc", "-", "203.0.113.7")


def test_with_the_limits_off_nothing_is_counted() -> None:
    configure_rate_limits(enabled=False, bookings="10/hour", reads="60/minute", webhooks="1/minute")

    assert all(within_webhook_limit("wxcc", "acme", "203.0.113.7") for _ in range(5))


def test_every_webhook_route_goes_through_the_gate(settings: Settings) -> None:
    """ADR 0124 point 1: a ``/webhooks/`` route without the gate fails here, like an admin route
    without a permission fails the authorisation sweep."""
    routes = [r for r in all_routes(create_app(settings).routes) if r.path.startswith("/webhooks/")]

    assert {r.path for r in routes} == {
        "/webhooks/webex",
        "/webhooks/wxcc",
        "/webhooks/contact-center/{kind}",
        "/webhooks/contact-center/{kind}/{org}",
    }
    for route in routes:
        assert len(guards(route, WebhookGate)) == 1, route.path
        assert route.methods == {"POST"}, route.path


def test_the_sweep_sees_a_route_without_the_gate() -> None:
    """The sweep's own check: a route that declares no gate has no guard to find."""
    router = APIRouter(prefix="/webhooks")

    @router.post("/new")
    async def new() -> None:
        """A route that forgot the gate."""

    (route,) = router.routes
    assert isinstance(route, APIRoute)
    assert guards(route, WebhookGate) == []
