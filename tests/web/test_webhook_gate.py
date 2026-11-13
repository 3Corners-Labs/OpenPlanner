"""The webhook gate's parts that need no database: the limit's buckets, the sweep, the labels."""

from collections.abc import Iterator

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from starlette.routing import Mount

from app.admin.audit_routes import ACTION_REASON_LABELS
from app.config import AGENT_API_PREFIX, Settings
from app.domain.inbound_gate import Refusal
from app.domain.security_log import INVALID_SIGNATURE, WEBHOOK_REJECTED
from app.i18n.po import load_catalogues
from app.main import create_app
from app.web.protection import (
    RateLimits,
    configure_rate_limits,
    webhook_retry_after,
    within_refused_limit,
    within_unknown_address_limit,
    within_webhook_limit,
)
from app.web.route_dependencies import all_routes, dependency_calls
from app.web.webhook_gate import is_webhook_gate


@pytest.fixture(autouse=True)
def _restore_limits() -> Iterator[None]:
    """Leave the process-wide limiter as the other tests expect it."""
    yield
    configure_rate_limits(RateLimits(), enabled=False)


def _limits(webhooks: str, *, enabled: bool = True) -> None:
    configure_rate_limits(RateLimits(webhooks=webhooks), enabled=enabled)


def test_a_bucket_is_one_platform_one_organisation_and_one_address() -> None:
    _limits("2/minute")

    assert within_webhook_limit("wxcc", "acme", "203.0.113.7")
    assert within_webhook_limit("wxcc", "acme", "203.0.113.7")
    assert not within_webhook_limit("wxcc", "acme", "203.0.113.7")
    # Two organisations behind one caller address do not share a bucket (ADR 0124 point 5).
    assert within_webhook_limit("wxcc", "voorbeeld", "203.0.113.7")
    # Nor do two platforms, two callers, or the installation's own address.
    assert within_webhook_limit("webex", "acme", "203.0.113.7")
    assert within_webhook_limit("wxcc", "acme", "203.0.113.8")
    assert within_webhook_limit("wxcc", "-", "203.0.113.7")


def test_the_three_buckets_are_apart() -> None:
    """Verified, refused and unknown-address calls never use each other's budget."""
    _limits("1/minute")

    assert within_refused_limit("wxcc", "acme", "203.0.113.7")
    assert not within_refused_limit("wxcc", "acme", "203.0.113.7")
    assert within_webhook_limit("wxcc", "acme", "203.0.113.7")
    assert within_unknown_address_limit("203.0.113.7")
    assert not within_unknown_address_limit("203.0.113.7")
    assert within_unknown_address_limit("203.0.113.8")


def test_with_the_limits_off_nothing_is_counted() -> None:
    _limits("1/minute", enabled=False)

    for _ in range(5):
        assert within_webhook_limit("wxcc", "acme", "203.0.113.7")
        assert within_refused_limit("wxcc", "acme", "203.0.113.7")
        assert within_unknown_address_limit("203.0.113.7")


def test_retry_after_is_the_window_of_the_limit() -> None:
    _limits("600/minute")
    assert webhook_retry_after() == "60"
    _limits("10/hour")
    assert webhook_retry_after() == "3600"


def test_the_limits_come_from_the_settings_in_one_place(settings: Settings) -> None:
    changed = settings.model_copy(
        update={"rate_limit_webhook_per_minute": 90, "rate_limit_bookings": "7/hour"}
    )

    limits = RateLimits.from_settings(changed)

    assert (limits.webhooks, limits.bookings) == ("90/minute", "7/hour")
    assert limits.reports == f"{changed.reporting_api_rate_per_minute}/minute"


MOUNTS_TODAY = {"/static", AGENT_API_PREFIX}
"""The sub-applications the application mounts today: the static files and, when switched on,
the machine API. Their routes are not seen as ``APIRoute``s of the application, so the sweep
cannot look inside them; a new mount must be named here on purpose."""


def _webhook_path(path: str) -> bool:
    return path == "/webhooks" or path.startswith("/webhooks/")


def test_every_webhook_route_goes_through_the_gate(settings: Settings) -> None:
    """ADR 0124 point 1: a ``/webhooks`` route without the gate fails here, like an admin route
    without a permission fails the authorisation sweep."""
    app = create_app(settings)
    routes = [r for r in all_routes(app.routes) if _webhook_path(r.path)]

    assert {r.path for r in routes} == {
        "/webhooks/webex",
        "/webhooks/wxcc",
        "/webhooks/contact-center/{kind}",
        "/webhooks/contact-center/{kind}/{org}",
    }
    for route in routes:
        gates = [call for call in dependency_calls(route) if is_webhook_gate(call)]
        assert len(gates) == 1, route.path
        assert route.methods == {"POST"}, route.path


def test_no_webhook_route_can_hide_in_a_mount(settings: Settings) -> None:
    """The sweep sees ``APIRoute``s only: a mounted sub-application would hide its routes."""
    mounts = {r.path for r in create_app(settings).routes if isinstance(r, Mount)}

    assert mounts <= MOUNTS_TODAY, f"a new mount hides its routes from the sweep: {mounts}"
    assert not any(_webhook_path(path) for path in mounts)


def test_the_sweep_catches_a_route_at_exactly_webhooks() -> None:
    assert _webhook_path("/webhooks")
    assert _webhook_path("/webhooks/x")
    assert not _webhook_path("/webhooks-other")


def test_the_sweep_sees_a_route_without_the_gate() -> None:
    """The sweep's own check: a route that declares no gate has no gate to find."""
    router = APIRouter(prefix="/webhooks")

    @router.post("/new")
    async def new() -> None:
        """A route that forgot the gate."""

    (route,) = router.routes
    assert isinstance(route, APIRoute)
    assert not any(is_webhook_gate(call) for call in dependency_calls(route))


def test_every_reason_but_the_old_one_has_a_label_in_both_languages() -> None:
    """The enum, the log catalogue and the audit labels cannot drift apart."""
    labelled = {reason for action, reason in ACTION_REASON_LABELS if action == WEBHOOK_REJECTED}

    assert labelled == {r.value for r in Refusal} - {INVALID_SIGNATURE}
    catalogues = load_catalogues()
    for locale in ("nl", "en"):
        for key in ACTION_REASON_LABELS.values():
            assert catalogues[locale].get(key), (locale, key)
