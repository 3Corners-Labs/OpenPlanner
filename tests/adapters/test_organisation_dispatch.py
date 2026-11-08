"""Each organisation's own WxCC dispatcher, signed in with its own Service App (ADR 0032)."""

import json

import httpx

from app.adapters.contact_center_connections import SealedConnections
from app.adapters.organisation_dispatch import OrganisationWxcc
from app.domain.calls import TaskState
from app.domain.contact_center import ConnectionChange, ContactCenterKind, Registration
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeSecrets
from tests.support.domain import NOW

WXCC = ContactCenterKind.WEBEX_CC
OAUTH = "https://webexapis.com/v1/access_token"
OWN = ConnectionChange(WXCC, "https://api.wxcc-eu2.cisco.com", "own-client", "secret", "refresh")


def _wxcc(seen: list[httpx.Request]) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == OAUTH:
            return httpx.Response(
                200,
                json={"access_token": "own-access", "expires_in": 3600, "refresh_token": "next"},
            )
        return httpx.Response(404)

    return httpx.MockTransport(answer)


async def _organisations(
    secrets: FakeSecrets, seen: list[httpx.Request]
) -> tuple[OrganisationWxcc, SealedConnections]:
    connections = SealedConnections(secrets)
    http = httpx.AsyncClient(transport=_wxcc(seen))
    return OrganisationWxcc(connections, secrets, http, FixedClock(NOW), OAUTH), connections


async def test_the_organisation_signs_in_with_its_own_client_on_its_own_host() -> None:
    secrets, seen = FakeSecrets(), []  # type: list[httpx.Request]
    organisations, connections = await _organisations(secrets, seen)
    await connections.save(1, OWN, NOW)

    own = await organisations.own(1, WXCC)
    assert own is not None
    dispatcher = own.dispatcher
    assert dispatcher is not None
    snapshot = await dispatcher.read_task("t-1")

    assert snapshot.state is TaskState.NONE  # the 404 of the mock
    oauth, read = seen
    assert b"client_id=own-client" in oauth.content
    assert b"refresh_token=refresh" in oauth.content
    assert read.url.host == "api.wxcc-eu2.cisco.com"
    assert read.headers["authorization"] == "Bearer own-access"
    assert secrets.values["contact-center-tokens:1"]["access_token"] == "own-access"


async def test_the_dispatcher_is_reused_until_the_connection_changes() -> None:
    secrets, seen = FakeSecrets(), []  # type: list[httpx.Request]
    organisations, connections = await _organisations(secrets, seen)
    await connections.save(1, OWN, NOW)

    first = await organisations.own(1, WXCC)
    again = await organisations.own(1, WXCC)
    await connections.save(
        1, ConnectionChange(WXCC, "https://api.wxcc-eu2.cisco.com", "other-client"), NOW
    )
    changed = await organisations.own(1, WXCC)

    assert first is not None
    assert again is not None
    assert changed is not None
    assert first.dispatcher is again.dispatcher
    assert changed.dispatcher is not first.dispatcher


async def test_without_a_complete_connection_there_is_no_dispatcher() -> None:
    secrets, seen = FakeSecrets(), []  # type: list[httpx.Request]
    organisations, connections = await _organisations(secrets, seen)

    assert await organisations.own(1, WXCC) is None
    await connections.save(
        1, ConnectionChange(WXCC, "https://api.wxcc-eu2.cisco.com", "client", "secret"), NOW
    )

    own = await organisations.own(1, WXCC)
    assert own is not None
    assert own.dispatcher is None  # no refresh token yet: its tasks wait
    assert seen == []


ADDRESS = "https://openplanner.example/webhooks/contact-center/webex_cc/demo"


def _platform(seen: list[httpx.Request], listed: object, create: int = 200) -> httpx.MockTransport:
    """A WxCC whose subscription listing is ``listed`` and whose create answers ``create``."""

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == OAUTH:
            return httpx.Response(200, json={"access_token": "own-access", "expires_in": 3600})
        if request.method == "GET":
            return httpx.Response(200, json=listed)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(create, json={"id": "new"})

    return httpx.MockTransport(answer)


async def _registering(
    listed: object, create: int = 200
) -> tuple[OrganisationWxcc, SealedConnections, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    connections = SealedConnections(FakeSecrets())
    http = httpx.AsyncClient(transport=_platform(seen, listed, create))
    organisations = OrganisationWxcc(
        connections,
        FakeSecrets(),
        http,
        FixedClock(NOW),
        OAUTH,
        webhook_base="https://openplanner.example",
    )
    return organisations, connections, seen


async def test_registering_replaces_the_subscription_at_the_organisations_address() -> None:
    """ADR 0032 CC-4d: a new secret takes effect; another address is never touched."""
    listed = {
        "data": [
            {"id": "old-1", "destinationUrl": ADDRESS},
            {"id": "theirs", "destinationUrl": "https://elsewhere.example/hook"},
            {"id": "../v2/x", "destinationUrl": ADDRESS},
        ]
    }
    organisations, connections, seen = await _registering(listed)
    assert await organisations.register(1, "demo") is Registration.NOT_READY
    await connections.save(1, OWN, NOW)

    assert await organisations.register(1, "demo") is Registration.REGISTERED

    assert [r.url.path for r in seen if r.method == "DELETE"] == ["/v2/subscriptions/old-1"]
    [created] = [r for r in seen if r.method == "POST" and r.url.path == "/v2/subscriptions"]
    body = json.loads(created.content)
    assert body["destinationUrl"] == ADDRESS
    assert body["secret"] == await connections.webhook_secret(1, WXCC)


async def test_a_refused_create_or_an_odd_listing_is_refused_and_an_outage_failed() -> None:
    for listed, create, expected in (
        ({"data": []}, 403, Registration.REFUSED),
        (["not", "an", "object"], 200, Registration.REFUSED),
        ({"data": []}, 503, Registration.FAILED),
    ):
        organisations, connections, seen = await _registering(listed, create)
        await connections.save(1, OWN, NOW)

        assert await organisations.register(1, "demo") is expected, (listed, create)
        if expected is Registration.REFUSED and create == 200:
            changes = [r for r in seen if r.method == "DELETE" or r.url.path == "/v2/subscriptions"]
            assert [r.method for r in changes] == ["GET"]  # nothing deleted, nothing created
