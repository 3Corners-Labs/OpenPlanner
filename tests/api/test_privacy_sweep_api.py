"""The machine API answers carry no personal data and no other tenant (WP-B2, ASVS V8).

Bookings whose personal-data fields hold unique markers (the same as the admin sweep) are
stored in the groups of two organisations. Every route of the agent API (ADR 0023) is then
called by a client of each organisation: an answer may name its own groups only and never a
marker, and the configuration package (ADR 0089 point 7), an installation export for a client
with the scope ``configuration:transfer``, holds no booking, no token and no secret of a client.

The routes are discovered from the mounted application: a route that addresses a group, or
any route missing from ``SWEPT``, fails the registry test until it is added here.
"""

import pytest
from fastapi.routing import APIRoute

from app.domain.services import BookingService, ContactInput
from tests.admin.test_privacy_sweep import MARKERS
from tests.api import test_agent_api
from tests.api.test_agent_api import (
    BASE,
    OPEN_ROUTES,
    PASSPHRASE,
    TRANSFER,
    Agent,
)
from tests.support.domain import SLOT_0900, book_confirmed
from tests.support.routes import all_routes, scoped_routes

agent = test_agent_api.agent  # the fixture of the API tests, collected here by name
OTHER = "andere-team"
WINDOW = {"from": "2026-01-05", "to": "2026-01-06"}
SWEPT = frozenset(
    {
        "GET /me",
        "GET /groups",
        "GET /groups/{slug}/availability",
        "GET /configuration",
        "PUT /configuration",
    }
)
"""Every route of the agent API except the open ones (token, docs); a new one is added here."""


def _participant(number: int) -> ContactInput:
    return ContactInput(
        first_name=MARKERS["first_name"],
        last_name=MARKERS["last_name"],
        email=f"{MARKERS['email']}-{number}@example.org",
        phone="06 2468 1357",
    )


@pytest.fixture
async def booked(agent: Agent) -> Agent:
    """The agent API with one marker booking in the group of each organisation."""
    service: BookingService = agent.app.state.booking_service
    for number, slug in enumerate(("demo", OTHER)):
        await book_confirmed(
            service, group_slug=slug, start=SLOT_0900, contact=_participant(number), locale="nl"
        )
    return agent


def _markers_in(text: str) -> set[str]:
    return {marker for marker in MARKERS.values() if marker in text}


def _routes(agent: Agent) -> list[APIRoute]:
    mounted = next(r for r in agent.app.routes if getattr(r, "path", None) == BASE)
    return all_routes(mounted.app.routes)  # type: ignore[attr-defined]


def test_every_route_of_the_agent_api_is_swept_and_only_one_addresses_a_group(agent: Agent) -> None:
    routes = _routes(agent)

    keys = {f"{m} {r.path}" for r in routes if r.path not in OPEN_ROUTES for m in r.methods}
    assert keys == SWEPT, "a new agent API route: sweep it in this file"
    assert set(scoped_routes(routes)) == {"GET /groups/{slug}/availability"}


@pytest.mark.parametrize(
    ("organisation", "groups", "own", "other"),
    [("demo", [], "demo", OTHER), ("andere", [OTHER], OTHER, "demo")],
)
async def test_the_answers_hold_no_marker_and_only_the_clients_own_groups(
    booked: Agent, organisation: str, groups: list[str], own: str, other: str
) -> None:
    issued = await booked.register(organisation=organisation, groups=groups)
    headers = await booked.bearer(issued)

    me = await booked.client.get(f"{BASE}/me", headers=headers)
    listing = await booked.client.get(f"{BASE}/groups", headers=headers)
    mine = await booked.client.get(
        f"{BASE}/groups/{own}/availability", params=WINDOW, headers=headers
    )
    theirs = await booked.client.get(
        f"{BASE}/groups/{other}/availability", params=WINDOW, headers=headers
    )

    assert [r.status_code for r in (me, listing, mine)] == [200, 200, 200]
    assert mine.json()["slots"], "no slot: the sweep would prove nothing"
    assert [g["slug"] for g in listing.json()] == [own]
    assert (theirs.status_code, theirs.json()) == (403, {"error": "insufficient_scope"})
    for response in (me, listing, mine, theirs):
        assert _markers_in(response.text) == set(), "personal data in a machine API answer"
    for response in (me, listing, mine):
        assert other not in response.text, "the other tenant in an answer"


async def test_the_configuration_package_holds_no_booking_no_token_and_no_client_secret(
    booked: Agent,
) -> None:
    issued = await booked.register(organisation="andere", groups=[OTHER], **TRANSFER)
    headers = {**await booked.bearer(issued), "x-passphrase": PASSPHRASE}

    exported = await booked.client.get(f"{BASE}/configuration", headers=headers)

    assert exported.status_code == 200, exported.text
    package = exported.content
    assert _markers_in(package.decode("latin-1")) == set(), "personal data in the package"
    token = headers["authorization"].removeprefix("Bearer ")
    for secret in (issued.secret, token, PASSPHRASE):
        assert secret.encode() not in package, "a credential in clear text in the package"
    assert issued.client.client_id.encode() in package, "the client itself is configuration"
