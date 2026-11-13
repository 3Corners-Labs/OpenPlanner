"""Immediate work and the check-in on the own queue page, against SQLite (ADR 0121 step 7).

A request without an appointment of the demo service, on a counted pool staffed Fridays
08:00–17:00 for both kinds of work, is listed under the appointments with the first name only;
*Pick up next* claims it and opens its page, which offers *Start* and then *Complete*. An
appointment that is request work offers *Check in*.
"""

from app.domain.authorization import Scope
from app.domain.flow_resolution import FlowRef
from app.domain.immediate_intake import new_access
from app.domain.service_requests import RequestPersonalData, on_demand_request
from tests.admin.conftest import AGENT, SAME_ORIGIN, Site
from tests.support.admin_site import site_uow
from tests.support.queue import own_queue, request_work, staffed_pool_flow

QUEUE = "/admin/groups/demo/queue"
EVERYTHING = Scope(everything=True)


async def _immediate(site: Site) -> int:
    """Give the demo service a pool flow and one waiting request without an appointment."""
    await own_queue(site, meeting=False, queue_free_pick_allowed=False)
    pool, flow_id = await staffed_pool_flow(site)
    async with site_uow(site).locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        access, _ = new_access("k" * 43)
        request = on_demand_request(
            organisation_id=group.organisation_id,
            group_id=group.id,
            flow=FlowRef(flow_id, 1),
            pool_id=pool,
            subject=None,
            access=access,
            now=site.clock.now(),
            unresolved_hours=24,
        )
        stored = await tx.add_service_request(
            request, RequestPersonalData(first_name="Grace", email="grace@voorbeeld.nl")
        )
    return stored.id or 0


async def test_immediate_work_is_picked_up_started_and_completed(site: Site) -> None:
    request_id = await _immediate(site)
    await site.login_as(AGENT)

    page = (await site.client.get(QUEUE)).text
    picked = await site.client.post(f"{QUEUE}/requests/next", headers=SAME_ORIGIN)

    assert "Zonder afspraak" in page
    assert "<td>Grace</td>" in page
    assert "grace@voorbeeld.nl" not in page
    assert picked.status_code == 303
    assert picked.headers["location"] == f"{QUEUE}/requests/{request_id}"

    opened = (await site.client.get(f"{QUEUE}/requests/{request_id}")).text
    assert f"{QUEUE}/requests/{request_id}/start" in opened
    assert "grace@voorbeeld.nl" not in opened

    started = await site.client.post(f"{QUEUE}/requests/{request_id}/start", headers=SAME_ORIGIN)
    running = (await site.client.get(f"{QUEUE}/requests/{request_id}")).text
    done = await site.client.post(f"{QUEUE}/requests/{request_id}/complete", headers=SAME_ORIGIN)

    assert started.status_code == 303
    assert f"{QUEUE}/requests/{request_id}/complete" in running
    assert done.headers["location"].endswith("done=completed")


async def test_a_refusal_is_explained_on_the_list(site: Site) -> None:
    request_id = await _immediate(site)
    await site.login_as(AGENT)

    chosen = await site.client.post(f"{QUEUE}/requests/{request_id}/claim", headers=SAME_ORIGIN)
    unknown = await site.client.get(f"{QUEUE}/requests/{request_id + 999}")

    assert chosen.status_code == 409  # free picking is off for the demo service here
    assert "Zonder afspraak" in chosen.text  # the queue, with the reason
    assert unknown.status_code == 409
    assert "Zonder afspraak" in unknown.text


async def test_an_appointment_of_request_work_is_checked_in(site: Site) -> None:
    booking_id = await request_work(site)
    await site.login_as(AGENT)

    checked = await site.client.post(f"{QUEUE}/{booking_id}/check-in", headers=SAME_ORIGIN)

    assert checked.status_code == 303
    assert checked.headers["location"].endswith("done=checked_in")
