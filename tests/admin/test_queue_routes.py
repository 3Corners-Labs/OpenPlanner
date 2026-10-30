"""The own queue in the admin portal (ADR 0043)."""

from app.domain.authorization import Principal, Role, Scope
from tests.admin.conftest import AGENT, GROUP_ADMIN, SAME_ORIGIN, Site
from tests.support.queue import own_queue as _own_queue
from tests.support.queue import site_uow as _uow

QUEUE = "/admin/groups/demo/queue"
COLLEAGUE = Principal(
    "sub-colleague", "Cas Collega", frozenset({Role.AGENT}), frozenset(), frozenset({"demo"})
)


async def test_the_queue_shows_first_names_and_hides_the_meeting_link(site: Site) -> None:
    await _own_queue(site)
    await site.login_as(AGENT)

    overview = (await site.client.get("/admin/groups/demo")).text
    text = (await site.client.get(QUEUE)).text

    assert f'href="{QUEUE}"' in overview  # the tab
    assert "<td>Ada<br>" in text
    assert "Lovelace" not in text
    assert "ada@example.org" not in text
    assert "webex.example" not in text
    assert "Volgende oppakken" in text
    assert 'aria-live="polite"' in text


async def test_pick_up_next_opens_the_meeting_for_that_agent_only(site: Site) -> None:
    booking_id = await _own_queue(site)
    await site.login_as(AGENT)

    picked = await site.client.post(f"{QUEUE}/next", headers=SAME_ORIGIN)
    page = (await site.client.get(f"{QUEUE}/{booking_id}")).text
    await site.login_as(COLLEAGUE)
    again = await site.client.post(f"{QUEUE}/next", headers=SAME_ORIGIN)
    other = await site.client.get(f"{QUEUE}/{booking_id}")

    assert picked.status_code == 303
    assert picked.headers["location"] == f"{QUEUE}/{booking_id}"
    assert 'href="https://webex.example/j/1"' in page
    assert again.status_code == 409
    assert "Er is geen afspraak meer om op te pakken." in again.text
    assert other.status_code == 409
    assert "webex.example" not in other.text
    async with _uow(site).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=5)
    assert any(e.event.detail == "queue.claimed" for e in events)


async def test_a_group_admin_releases_a_colleagues_claim(site: Site) -> None:
    booking_id = await _own_queue(site)
    await site.login_as(AGENT)
    await site.client.post(f"{QUEUE}/next", headers=SAME_ORIGIN)
    await site.login_as(GROUP_ADMIN)

    listing = (await site.client.get(QUEUE)).text
    released = await site.client.post(f"{QUEUE}/{booking_id}/release", headers=SAME_ORIGIN)

    assert "Opgepakt door een collega" in listing
    assert f'action="{QUEUE}/{booking_id}/release"' in listing
    assert released.status_code == 303
    assert "Volgende oppakken" in (await site.client.get(QUEUE)).text


async def test_a_chosen_row_only_with_free_pick(site: Site) -> None:
    booking_id = await _own_queue(site)
    await site.login_as(AGENT)

    refused = await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)

    assert refused.status_code == 409
    assert "altijd de volgende" in refused.text
    assert f'action="{QUEUE}/{booking_id}/claim"' not in refused.text


async def test_free_pick_shows_a_button_per_row(site: Site) -> None:
    booking_id = await _own_queue(site, queue_free_pick_allowed=True)
    await site.login_as(AGENT)

    listing = (await site.client.get(QUEUE)).text
    claimed = await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)

    assert f'action="{QUEUE}/{booking_id}/claim"' in listing
    assert claimed.status_code == 303
