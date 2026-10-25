import json
from collections.abc import Callable

import httpx

from app.adapters.webex_auth import AuthorizedClient
from app.adapters.webhook_registration import register_webex, register_wxcc
from tests.fakes.tokens import authorized_client

TARGET = "https://afspraak.example.nl/webhooks/webex"


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[AuthorizedClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return authorized_client(record), seen


async def test_only_missing_webex_webhooks_are_created() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"items": [{"targetUrl": TARGET, "resource": "meetings", "event": "started"}]},
            )
        return httpx.Response(200, json={"id": "w"})

    client, seen = _client(handler)
    report = await register_webex(client, "https://webexapis.com/v1", TARGET, "secret")

    assert report == [
        "meetings/started: exists",
        "meetings/ended: created",
        "meetingParticipants/joined: created",
        "meetingParticipants/left: created",
    ]
    created = [json.loads(r.content) for r in seen if r.method == "POST"]
    assert {(c["resource"], c["event"]) for c in created} == {
        ("meetings", "ended"),
        ("meetingParticipants", "joined"),
        ("meetingParticipants", "left"),
    }
    assert all(c["ownedBy"] == "org" and c["secret"] == "secret" for c in created)


async def test_wxcc_subscription_is_created_once() -> None:
    target = "https://afspraak.example.nl/webhooks/wxcc"
    subscriptions: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": subscriptions, "meta": {}})
        subscriptions.append(json.loads(request.content))
        return httpx.Response(201, json={"id": "s"})

    client, _ = _client(handler)

    first = await register_wxcc(client, "https://api.wxcc-eu1.cisco.com", target, "secret")
    second = await register_wxcc(client, "https://api.wxcc-eu1.cisco.com", target, "secret")

    assert (first, second) == ("wxcc subscription: created", "wxcc subscription: exists")
    assert subscriptions[0]["eventTypes"] == [
        "task:new",
        "task:connected",
        "task:ended",
        "task:failed",
    ]


async def test_a_refused_webex_webhook_is_reported_and_the_rest_still_tried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"items": []})
        resource = json.loads(request.content)["resource"]
        return httpx.Response(403 if resource == "meetings" else 200, json={"id": "w"})

    client, seen = _client(handler)
    report = await register_webex(client, "https://webexapis.com/v1", TARGET, "secret")

    assert report[0] == (
        "meetings/started: refused (rejected_403): needs meeting:admin_schedule_read, "
        "authorised by a Webex site admin or Compliance Officer"
    )
    assert report[2] == "meetingParticipants/joined: created"
    assert len([r for r in seen if r.method == "POST"]) == 4
    assert all("secret" not in line for line in report)


async def test_a_refused_wxcc_subscription_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.method == "GET" else 403, json={"data": []})

    client, _ = _client(handler)

    report = await register_wxcc(
        client, "https://api.wxcc-eu1.cisco.com", "https://x.nl/webhooks/wxcc", "secret"
    )

    assert report == "wxcc subscription: refused (rejected_403): needs cjp:config_write"
