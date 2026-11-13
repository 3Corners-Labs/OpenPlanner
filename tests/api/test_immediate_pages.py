"""Immediate intake for participants (ADR 0121 step 7): the form, the request page, withdrawing.

The demo service gets a flow ``nu`` on a counted pool staffed Thursdays 08:00–12:00 Amsterdam
for appointments and work without appointment. With the rollback gate shut the address keeps
the legacy direct call; opened by a monkeypatch, ``/g/demo/direct?flow=nu`` takes a request
without an appointment, which the browser then finds under ``/r/{ref}`` by its cookie only.
"""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
import structlog.testing

from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.scheduling import Offer
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.flows import engine_of
from tests.support.participant import ADA, SAME_ORIGIN
from tests.support.pool_flows import block, open_gate, seed_pool_flow

FORM = {**ADA, "flow": "nu"}


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        uow = SqlUnitOfWork(engine_of(client))
        async with uow.read() as tx:
            group = await tx.get_group("demo")
        assert group is not None
        thursday = block(weekday=3, opens="08:00", closes="12:00", offer=Offer.BOTH)
        await seed_pool_flow(
            uow, group.organisation_id, group.id, [thursday], code="nu", on_demand=True
        )
        yield client


def key_of(page: str) -> str:
    """Return the intake key the form carries."""
    found = re.search(r'name="intake_key" value="([^"]+)"', page)
    assert found is not None
    return found.group(1)


async def ask(site: httpx.AsyncClient) -> httpx.Response:
    """Open the form and send it, as a browser does."""
    form = await site.get("/g/demo/direct", params={"flow": "nu"})
    assert form.status_code == 200, form.text
    data = {**FORM, "intake_key": key_of(form.text)}
    return await site.post("/g/demo/direct", data=data, headers=SAME_ORIGIN)


async def test_with_the_gate_shut_the_flow_is_refused_and_the_direct_call_stays(
    site: httpx.AsyncClient,
) -> None:
    named = await site.get("/g/demo/direct", params={"flow": "nu"})
    plain = await site.get("/g/demo/direct")

    assert named.status_code == 422
    assert 'name="intake_key"' not in plain.text


async def test_a_request_is_taken_in_and_shown_by_its_cookie(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_gate(monkeypatch)
    form = await site.get("/g/demo/direct", params={"flow": "nu"})
    assert "Nu hulp vragen" in form.text
    assert 'name="website"' in form.text  # the honeypot

    with structlog.testing.capture_logs() as logs:
        sent = await ask(site)

    assert sent.status_code == 303
    target = sent.headers["location"]
    assert re.fullmatch(r"/r/[A-Za-z0-9_-]{16,32}", target)
    assert "Ada" not in target
    assert "ada@example.org" not in repr(logs)
    page = await site.get(target)
    assert page.status_code == 200
    assert "U staat in de rij." in page.text
    assert "Uw plaats in de rij: 1." in page.text
    assert "ada@example.org" not in page.text

    cookie = sent.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "ada" not in cookie

    stranger = httpx.AsyncClient(transport=site._transport, base_url=str(site.base_url))
    async with stranger:
        refused = await stranger.get(target)
        withdrawn = await stranger.post(f"{target}/leave", headers=SAME_ORIGIN)
        unknown = await site.get("/r/unknown-reference")
    assert refused.status_code == unknown.status_code == withdrawn.status_code
    assert refused.status_code >= 400
    still = await site.get(target)
    assert "U staat in de rij." in still.text  # the stranger withdrew nothing


async def test_a_retried_post_and_a_second_visit_give_the_same_request(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A16 and choice I12."""
    open_gate(monkeypatch)
    form = await site.get("/g/demo/direct", params={"flow": "nu"})
    data = {**FORM, "intake_key": key_of(form.text)}
    first = await site.post("/g/demo/direct", data=data, headers=SAME_ORIGIN)
    again = await site.post("/g/demo/direct", data=data, headers=SAME_ORIGIN)
    visit = await site.get("/g/demo/direct", params={"flow": "nu"})

    assert first.headers["location"] == again.headers["location"]
    assert visit.status_code == 303
    assert visit.headers["location"] == first.headers["location"]


async def test_the_participant_withdraws_and_may_ask_again(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_gate(monkeypatch)
    target = (await ask(site)).headers["location"]

    question = await site.get(f"{target}/leave")
    left = await site.post(f"{target}/leave", headers=SAME_ORIGIN)
    page = await site.get(target)
    status = await site.get(f"{target}/status")
    fresh = await site.get("/g/demo/direct", params={"flow": "nu"})

    assert "Verzoek intrekken?" in question.text
    assert left.status_code == 303
    assert "U heeft uw verzoek ingetrokken." in page.text
    assert "Opnieuw hulp vragen" in page.text
    assert status.status_code == 200
    assert fresh.status_code == 200  # a finished request does not hold the browser


async def test_a_filled_honeypot_and_a_foreign_origin_store_nothing(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_gate(monkeypatch)
    form = await site.get("/g/demo/direct", params={"flow": "nu"})
    data = {**FORM, "intake_key": key_of(form.text)}

    bot = await site.post("/g/demo/direct", data={**data, "website": "x"}, headers=SAME_ORIGIN)
    foreign = await site.post(
        "/g/demo/direct", data=data, headers={"origin": "https://elders.example"}
    )

    assert bot.status_code == 400
    assert foreign.status_code == 403
    async with SqlUnitOfWork(engine_of(site)).read() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        assert await tx.open_immediate(group.id) == []


async def test_the_status_fragment_answers_204_while_unchanged(
    site: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_gate(monkeypatch)
    target = (await ask(site)).headers["location"]
    page = await site.get(target)
    poll = re.search(r'data-poll="([^"]+)"', page.text)
    assert poll is not None

    unchanged = await site.get(poll.group(1).replace("&amp;", "&"))

    assert unchanged.status_code == 204
