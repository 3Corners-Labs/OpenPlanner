import json

import httpx

from app.adapters.webex_auth import AuthorizedClient, ServiceAppConfig, WebexTokens
from app.adapters.webhook_registration import register_webex, register_wxcc
from tests.fakes.clock import FixedClock
from tests.fakes.tokens import MemoryTokenStore
from tests.support.domain import NOW

TARGET = "https://afspraak.example.nl/webhooks/webex"


def _client(handler: object) -> tuple[AuthorizedClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/access_token":
            return httpx.Response(200, json={"access_token": "a", "expires_in": 3600})
        seen.append(request)
        return handler(request)  # type: ignore[operator]

    http = httpx.AsyncClient(transport=httpx.MockTransport(route))
    tokens = WebexTokens(ServiceAppConfig("c", "s", "r"), http, MemoryTokenStore(), FixedClock(NOW))
    return AuthorizedClient(http, tokens), seen


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
