"""Skills in the own queue page, against SQLite (ADR 0121 step 4, choice S3, A08, A09, A35).

A booking made under a flow that requires a skill stays in the queue for an agent without it,
marked with the skill's name and without a *Claim* button; the claim is refused with a message;
granting the skill opens it, and revoking it after the claim stops the claimed page.
"""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import BookingRow
from tests.admin.conftest import AGENT, SAME_ORIGIN, SUPER, Site
from tests.support.admin_site import site_app
from tests.support.flow_seed import add_flow, publish_flow
from tests.support.flows import engine_of, flow_ids
from tests.support.queue import own_queue
from tests.support.skill_seed import add_pool, add_skill, staff_id

QUEUE = "/admin/groups/demo/queue"
NOT_FOR_YOU = "Niet voor u: ontbrekende vaardigheid: Duits (german)"


async def _booking_needing_german(site: Site) -> tuple[int, int]:
    """Book in the own queue under a published flow that requires *German*; return both ids."""
    booking_id = await own_queue(site, queue_free_pick_allowed=True)
    await site.login_as(SUPER)  # publishing records who published
    german = await add_skill(site, "german", "Duits", "German")
    pool = await add_pool(site, "Loket", (german,))
    await add_flow(site, "advies", pool, required_skill_ids=frozenset({german}))
    await publish_flow(site)
    engine = engine_of(site.client)
    flow_id = (await flow_ids(engine))["advies"]
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(flow_id=flow_id, flow_revision=1)
        )
    return booking_id, german


async def _grant(site: Site, *skills: int) -> None:
    """Give the agent (who has logged in) exactly ``skills`` in the organisation."""
    member = await staff_id(site, AGENT.subject)
    await site_app(site).state.skill_service.grant(SUPER, "demo", member, skills)


async def test_work_the_agent_lacks_a_skill_for_is_shown_but_cannot_be_claimed(
    site: Site,
) -> None:
    booking_id, german = await _booking_needing_german(site)
    await site.login_as(AGENT)

    page = (await site.client.get(QUEUE)).text
    claim = await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)
    pick = await site.client.post(f"{QUEUE}/next", headers=SAME_ORIGIN)

    assert "<td>Ada<br>" in page
    assert NOT_FOR_YOU in page
    assert f"{QUEUE}/{booking_id}/claim" not in page
    assert "Volgende oppakken" not in page
    for refused in (claim, pick):
        assert refused.status_code == 409
        assert "niet elke vaardigheid" in refused.text

    await _grant(site, german)
    page = (await site.client.get(QUEUE)).text
    claimed = await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)

    assert NOT_FOR_YOU not in page
    assert claimed.status_code == 303


async def test_a_revoked_skill_closes_the_claimed_page_and_offers_release(site: Site) -> None:
    booking_id, german = await _booking_needing_german(site)
    await site.login_as(AGENT)
    await _grant(site, german)
    await site.client.post(f"{QUEUE}/{booking_id}/claim", headers=SAME_ORIGIN)

    await _grant(site)
    opened = await site.client.get(f"{QUEUE}/{booking_id}")
    live = await site.client.get(f"{QUEUE}/{booking_id}/live")

    assert opened.status_code == 409
    assert "webex.example" not in opened.text
    assert "webex.example" not in live.text
    assert "niet elke vaardigheid" in live.text
    assert f"{QUEUE}/{booking_id}/release" in opened.text
    released = await site.client.post(f"{QUEUE}/{booking_id}/release", headers=SAME_ORIGIN)
    assert released.status_code == 303
