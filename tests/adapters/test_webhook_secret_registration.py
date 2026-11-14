"""A rotated secret reaches the platform without a gap in the events (ADR 0124 point 8).

The video platform's webhooks are updated in place; a WxCC subscription keeps its secret, so a new
one is made before the old one is removed. Both lean on the previous secret verifying for the
overlap, which is what makes this order safe.
"""

import json
from collections.abc import Callable

import httpx
import pytest

from app.adapters.webex_auth import AuthorizedClient
from app.adapters.webhook_registration import (
    WEBEX_EVENTS,
    register_webex,
    register_wxcc,
    renew_wxcc,
)
from app.domain.contact_center import Registration
from app.domain.ports import IntegrationError
from tests.fakes.tokens import authorized_client

TARGET = "https://afspraak.example.nl/webhooks/webex"
BASE = "https://webexapis.com/v1"
HOST = "host-demo@example.nl"
FILTER = f"hostEmail={HOST}"
WXCC_BASE = "https://api.wxcc-eu1.cisco.com"
WXCC_TARGET = "https://afspraak.example.nl/webhooks/contact-center/webex_cc/demo"


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[AuthorizedClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return authorized_client(record), seen


def _hook(resource: str, event: str, ident: str, filter_: str = FILTER) -> dict[str, object]:
    return {
        "id": ident,
        "targetUrl": TARGET,
        "resource": resource,
        "event": event,
        "filter": filter_,
    }


def _wxcc_platform(
    listed: list[dict[str, object]], create: Callable[[bool], int] = lambda old_exists: 201
) -> Callable[[httpx.Request], httpx.Response]:
    """A WxCC that holds ``listed`` and answers a create through ``create(an old one exists)``."""
    held = list(listed)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": held, "meta": {}})
        if request.method == "DELETE":
            gone = request.url.path.rsplit("/", 1)[1]
            held[:] = [item for item in held if item["id"] != gone]
            return httpx.Response(204)
        status = create(bool(held))
        if status < 300:
            held.append({"id": "new", "destinationUrl": WXCC_TARGET})
        return httpx.Response(status, json={})

    return handler


# --- WxCC: made anew, the new one first ---------------------------------------------------------


async def test_a_wxcc_subscription_is_made_before_the_old_one_is_removed() -> None:
    old = {"id": "old-1", "destinationUrl": WXCC_TARGET}
    client, seen = _client(_wxcc_platform([old]))

    outcome = await renew_wxcc(client, WXCC_BASE, WXCC_TARGET, "new-secret")

    assert outcome is Registration.REGISTERED
    assert [r.method for r in seen] == ["GET", "POST", "DELETE"]  # never none at the platform
    assert json.loads(seen[1].content)["secret"] == "new-secret"
    assert seen[2].url.path == "/v2/subscriptions/old-1"


async def test_when_wxcc_refuses_a_second_subscription_the_old_ones_go_first() -> None:
    """The fall-back of the unproven case: made anew, with a short gap the retry must cover."""
    old = {"id": "old-1", "destinationUrl": WXCC_TARGET}
    client, seen = _client(
        _wxcc_platform([old], create=lambda old_exists: 409 if old_exists else 201)
    )

    outcome = await renew_wxcc(client, WXCC_BASE, WXCC_TARGET, "new-secret")

    assert outcome is Registration.REGISTERED
    assert [r.method for r in seen] == ["GET", "POST", "DELETE", "POST"]


async def test_a_refused_create_with_nothing_to_fall_back_on_is_refused() -> None:
    client, seen = _client(_wxcc_platform([], create=lambda old_exists: 403))

    assert await renew_wxcc(client, WXCC_BASE, WXCC_TARGET, "s") is Registration.REFUSED
    assert [r.method for r in seen] == ["GET", "POST"]


async def test_an_outage_leaves_the_old_subscription_to_verify_under_the_previous_secret() -> None:
    old = {"id": "old-1", "destinationUrl": WXCC_TARGET}
    client, seen = _client(_wxcc_platform([old], create=lambda old_exists: 503))

    with pytest.raises(IntegrationError):
        await renew_wxcc(client, WXCC_BASE, WXCC_TARGET, "new-secret")

    assert "DELETE" not in [r.method for r in seen]


async def test_only_subscriptions_at_the_address_with_a_clean_id_are_removed() -> None:
    held = [
        {"id": "old-1", "destinationUrl": WXCC_TARGET},
        {"id": "theirs", "destinationUrl": "https://elsewhere.example/hook"},
        {"id": "../v2/x", "destinationUrl": WXCC_TARGET},
    ]
    client, seen = _client(_wxcc_platform(held))

    await renew_wxcc(client, WXCC_BASE, WXCC_TARGET, "s")

    assert [r.url.path for r in seen if r.method == "DELETE"] == ["/v2/subscriptions/old-1"]


async def test_an_odd_listing_is_refused_and_nothing_is_changed() -> None:
    client, seen = _client(lambda r: httpx.Response(200, json=["not", "an", "object"]))

    assert await renew_wxcc(client, WXCC_BASE, WXCC_TARGET, "s") is Registration.REFUSED
    assert [r.method for r in seen] == ["GET"]


async def test_the_installation_wxcc_subscription_is_renewed_with_the_flag() -> None:
    old = {"id": "old-1", "destinationUrl": WXCC_TARGET}
    client, seen = _client(_wxcc_platform([old]))

    renewed = await register_wxcc(client, WXCC_BASE, WXCC_TARGET, "new", update_secret=True)

    assert renewed == "wxcc subscription: renewed"
    assert [r.method for r in seen] == ["GET", "POST", "DELETE"]


async def test_a_refused_renewal_is_reported_without_the_secret() -> None:
    client, _ = _client(_wxcc_platform([], create=lambda old_exists: 403))

    report = await register_wxcc(client, WXCC_BASE, WXCC_TARGET, "new-secret", update_secret=True)

    assert report == "wxcc subscription: refused (see the request log)"


# --- the video platform: updated in place -------------------------------------------------------


async def test_the_video_platforms_webhooks_are_updated_in_place_with_the_new_secret() -> None:
    held = [_hook(resource, event, f"w-{event}") for resource, event in WEBEX_EVENTS]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"items": held})
        return httpx.Response(200, json={})

    client, seen = _client(handler)
    report = await register_webex(
        client, BASE, TARGET, "new-secret", hosts=[HOST], update_secret=True
    )

    assert report == [f"{resource}/{event} [{HOST}]: updated" for resource, event in WEBEX_EVENTS]
    puts = [r for r in seen if r.method == "PUT"]
    assert [r.url.path for r in puts] == [f"/v1/webhooks/w-{event}" for _, event in WEBEX_EVENTS]
    assert all(json.loads(r.content)["secret"] == "new-secret" for r in puts)
    assert all(json.loads(r.content)["targetUrl"] == TARGET for r in puts)
    assert not [r for r in seen if r.method == "POST"]  # nothing made anew
    assert all("new-secret" not in line for line in report)


async def test_a_webhook_missing_at_the_video_platform_is_still_created_when_updating() -> None:
    client, seen = _client(lambda r: httpx.Response(200, json={"items": [], "id": "w"}))

    report = await register_webex(client, BASE, TARGET, "s", hosts=[HOST], update_secret=True)

    assert all(line.endswith(": created") for line in report)
    assert len([r for r in seen if r.method == "POST"]) == 4


async def test_without_the_flag_an_existing_webhook_is_left_alone() -> None:
    held = [_hook(resource, event, f"w-{event}") for resource, event in WEBEX_EVENTS]
    client, seen = _client(lambda r: httpx.Response(200, json={"items": held}))

    report = await register_webex(client, BASE, TARGET, "s", hosts=[HOST])

    assert all(line.endswith(": exists") for line in report)
    assert [r.method for r in seen] == ["GET"]


async def test_a_refused_update_is_reported_and_the_rest_still_tried() -> None:
    held = [_hook(resource, event, f"w-{event}") for resource, event in WEBEX_EVENTS]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"items": held})
        return httpx.Response(403 if request.url.path.endswith("w-started") else 200, json={})

    client, seen = _client(handler)
    report = await register_webex(client, BASE, TARGET, "s", hosts=[HOST], update_secret=True)

    assert report[0].startswith(f"meetings/started [{HOST}]: refused (rejected_403)")
    assert report[1:] == [f"{r}/{e} [{HOST}]: updated" for r, e in WEBEX_EVENTS[1:]]
    assert len([r for r in seen if r.method == "PUT"]) == 4


async def test_an_update_with_a_bad_webhook_id_leaves_it_as_it_was() -> None:
    held = [_hook("meetings", "started", "../x")]
    client, seen = _client(lambda r: httpx.Response(200, json={"items": held, "id": "w"}))

    report = await register_webex(client, BASE, TARGET, "s", hosts=[HOST], update_secret=True)

    assert report[0] == f"meetings/started [{HOST}]: refused (no usable id): left as it was"
    assert not [r for r in seen if r.method == "PUT"]


async def test_a_retryable_failure_of_an_update_is_raised_for_the_caller() -> None:
    held = [_hook("meetings", "started", "w-1")]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"items": held})
        return httpx.Response(503, json={})

    client, _ = _client(handler)

    with pytest.raises(IntegrationError):
        await register_webex(client, BASE, TARGET, "s", hosts=[HOST], update_secret=True)
