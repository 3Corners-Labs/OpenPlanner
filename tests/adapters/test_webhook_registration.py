import json
from collections.abc import Callable

import httpx

from app.adapters.webex_auth import AuthorizedClient
from app.adapters.webhook_registration import WEBEX_EVENTS, register_webex, register_wxcc
from tests.fakes.tokens import authorized_client

TARGET = "https://afspraak.example.nl/webhooks/webex"
BASE = "https://webexapis.com/v1"
HOST_A = "host-demo@example.nl"
HOST_B = "host-paspoort@example.nl"
HOSTS = [HOST_A, HOST_B]
FILTER_A = f"hostEmail={HOST_A}"


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[AuthorizedClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return authorized_client(record), seen


async def test_webhooks_are_registered_per_host_account_and_only_when_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            existing = {"targetUrl": TARGET, "resource": "meetings", "event": "started"}
            return httpx.Response(200, json={"items": [existing | {"filter": FILTER_A}]})
        return httpx.Response(200, json={"id": "w"})

    client, seen = _client(handler)
    report = await register_webex(client, BASE, TARGET, "secret", hosts=HOSTS)

    assert report[:4] == [
        f"meetings/started [{HOST_A}]: exists",
        f"meetings/ended [{HOST_A}]: created",
        f"meetingParticipants/joined [{HOST_A}]: created",
        f"meetingParticipants/left [{HOST_A}]: created",
    ]
    assert len(report) == 8  # both hosts, four events each
    created = [json.loads(r.content) for r in seen if r.method == "POST"]
    assert len(created) == 7
    assert {c["filter"] for c in created} == {FILTER_A, f"hostEmail={HOST_B}"}
    assert all(c["ownedBy"] == "org" and c["secret"] == "secret" for c in created)
    assert all(HOST_A not in c["name"] and HOST_B not in c["name"] for c in created)


async def test_host_accounts_are_deduplicated_case_insensitively() -> None:
    client, seen = _client(lambda r: httpx.Response(200, json={"items": [], "id": "w"}))

    await register_webex(client, BASE, TARGET, "s", hosts=[HOST_A, HOST_A.upper(), " "])

    assert len([r for r in seen if r.method == "POST"]) == 4


async def test_without_host_accounts_nothing_is_registered_but_stale_ones_are_shown() -> None:
    stale = {"id": "w-old", "targetUrl": TARGET, "resource": "meetings", "event": "started"}
    client, seen = _client(lambda r: httpx.Response(200, json={"items": [stale]}))

    report = await register_webex(client, BASE, TARGET, "secret", hosts=[])

    assert report[0] == "webex: no group has a video host account; no webhooks registered"
    assert report[1].startswith("meetings/started [all meetings]: stale")
    assert [r for r in seen if r.method == "POST"] == []


async def test_unfiltered_org_wide_webhooks_are_reported_as_stale() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            stale = {"id": "w-old", "targetUrl": TARGET, "resource": "meetings", "event": "ended"}
            return httpx.Response(200, json={"items": [stale]})
        return httpx.Response(200, json={"id": "w"})

    client, _ = _client(handler)
    report = await register_webex(client, BASE, TARGET, "secret", hosts=[HOST_A])

    assert report[-1] == (
        "meetings/ended [all meetings]: stale (no hostEmail filter, every meeting of the "
        "organisation): delete webhook w-old"
    )


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
    assert subscriptions[0]["resourceVersion"] == "task:1.0.0"  # required by Subscriptions v2
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
    report = await register_webex(client, BASE, TARGET, "secret", hosts=[HOST_A])

    assert report[0] == (
        f"meetings/started [{HOST_A}]: refused (rejected_403): needs "
        "meeting:admin_schedule_read, authorised by a Webex site admin or Compliance Officer"
    )
    assert report[2] == f"meetingParticipants/joined [{HOST_A}]: created"
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


async def test_a_wxcc_validation_error_is_not_blamed_on_a_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.method == "GET" else 400, json={"data": []})

    client, _ = _client(handler)

    report = await register_wxcc(
        client, "https://api.wxcc-eu1.cisco.com", "https://x.nl/webhooks/wxcc", "secret"
    )

    assert report == (
        "wxcc subscription: refused (rejected_400): WxCC refused the request "
        "(destination must be https on port 443 with a public domain; secret 32+ characters)"
    )


async def test_org_owned_webhooks_are_listed_with_owned_by_org_and_409_means_exists() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url.params["ownedBy"] == "org"  # org webhooks are not listed otherwise
            return httpx.Response(200, json={"items": []})
        return httpx.Response(409, json={"message": "already exists"})

    client, _ = _client(handler)
    report = await register_webex(client, BASE, TARGET, "secret", hosts=[HOST_A])

    assert report[0] == f"meetings/started [{HOST_A}]: exists"


async def test_a_refused_listing_is_reported_and_registration_still_tried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(403)
        return httpx.Response(409)

    client, _ = _client(handler)
    report = await register_webex(client, BASE, TARGET, "secret", hosts=[HOST_A])

    assert report[0] == (
        "webhook listing: refused (rejected_403): needs meeting:admin_schedule_read, "
        "authorised by a Webex site admin or Compliance Officer"
    )
    assert report[1:] == [f"{r}/{e} [{HOST_A}]: exists" for r, e in WEBEX_EVENTS]
