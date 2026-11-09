"""The dispatcher is picked per platform (ADR 0032 point 5), like a sender per channel."""

from dataclasses import replace

import pytest

from app.domain.admin import AdminService
from app.domain.booking import MeetingRef
from app.domain.call_service import CallService
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef
from app.domain.integration import IntegrationService
from app.domain.ports import ContactCenterDispatchers, IntegrationError
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import (
    FakeDispatcher,
    FakeOrganisationDispatchers,
    FakeVideo,
    webex_cc,
)
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, make_group, webex_routing, window

GROUP = make_group(
    id=1,
    slug="balie",
    name="Balie",
    video_host_email="host@example.nl",
    contact_center=webex_routing("openplanner@example.biz", "openplanner-video", "team-1"),
)

WEBEX_CC = ContactCenterKind.WEBEX_CC
TASK_1 = ContactCenterTaskRef("task-1", WEBEX_CC)


async def _service(  # type: ignore[no-untyped-def]
    dispatchers: ContactCenterDispatchers,
    organisations: FakeOrganisationDispatchers | None = None,
):
    state = State()
    state.groups[1] = GROUP
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    uow = InMemoryUnitOfWork(state)
    clock = FixedClock(NOW)
    receipt = await book(BookingService(uow, clock, TEST_TOKEN_KEY))
    booking = replace(receipt.booking, meeting=MeetingRef("m-1", "https://site.webex.com/m"))
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    calls = CallService(
        uow,
        integration,
        FakeVideo(),
        dispatchers,
        lambda b, g: "label",
        organisations=organisations,
    )
    return uow, calls, booking


async def test_the_task_goes_to_the_dispatcher_of_the_groups_platform() -> None:
    dispatcher = FakeDispatcher()
    uow, calls, booking = await _service(webex_cc(dispatcher))

    done = await calls.ensure_task(booking, GROUP)

    [task] = dispatcher.items
    assert task.routing == GROUP.contact_center
    assert task.routing_value == "team-1"
    assert done.contact_center_task == TASK_1
    assert uow.state.bookings[booking.id or 0].contact_center_task == TASK_1


async def test_a_platform_without_a_dispatcher_is_skipped_like_no_contact_center() -> None:
    _, calls, booking = await _service({})

    assert not calls.dispatch_available
    assert not calls.end_available
    assert await calls.ensure_task(booking, GROUP) == booking


async def test_the_task_is_ended_by_the_dispatcher_of_the_platform_that_holds_it() -> None:
    dispatcher = FakeDispatcher()
    _, calls, booking = await _service(webex_cc(dispatcher))
    held = replace(booking, contact_center_task=ContactCenterTaskRef("t-9", WEBEX_CC))

    await calls.end_task(held, GROUP)

    assert dispatcher.marked == [("t-9", held.reference)]
    assert dispatcher.ended == ["t-9"]


async def test_a_task_without_a_dispatcher_for_its_platform_is_left_alone() -> None:
    uow, calls, booking = await _service({})
    held = replace(booking, contact_center_task=ContactCenterTaskRef("t-9", WEBEX_CC))
    booking_id = booking.id or 0
    status_before = uow.state.call_status.get(booking_id)
    events_before = list(uow.state.booking_events.get(booking_id, []))

    await calls.end_task(held, GROUP)  # nothing to call, nothing raised

    assert uow.state.booking_events.get(booking_id, []) == events_before  # no timeline event
    assert uow.state.call_status.get(booking_id) == status_before  # status unchanged


async def test_an_organisation_with_its_own_connection_dispatches_through_it() -> None:
    """ADR 0032 point 9: its own tenant, not the installation's."""
    own, installation = FakeDispatcher(), FakeDispatcher()
    organisations = FakeOrganisationDispatchers({GROUP.organisation_id: own})
    _, calls, booking = await _service(webex_cc(installation), organisations)

    await calls.ensure_task(booking, GROUP)
    await calls.end_task(replace(booking, contact_center_task=TASK_1), GROUP)

    assert len(own.items) == 1
    assert own.ended == ["task-1"]
    assert installation.items == []
    assert installation.ended == []


async def test_an_incomplete_own_connection_never_falls_back_to_the_installation() -> None:
    """A task of one tenant never goes to another: it waits for the connection."""
    installation = FakeDispatcher()
    organisations = FakeOrganisationDispatchers({GROUP.organisation_id: None})
    _, calls, booking = await _service(webex_cc(installation), organisations)

    with pytest.raises(IntegrationError) as waiting:
        await calls.ensure_task(booking, GROUP)
    assert waiting.value.code == "connection_incomplete"
    assert waiting.value.retryable  # the job retries; a final failure goes to the fallback
    assert installation.items == []


async def test_an_organisation_without_a_connection_uses_the_installations() -> None:
    """ADR 0032 point 11: the fallback for one minor."""
    installation = FakeDispatcher()
    _, calls, booking = await _service(webex_cc(installation), FakeOrganisationDispatchers())

    await calls.ensure_task(booking, GROUP)

    assert len(installation.items) == 1


async def test_only_organisation_connections_still_make_dispatch_available() -> None:
    _, calls, _ = await _service({}, FakeOrganisationDispatchers())

    assert calls.dispatch_available
    assert calls.end_available
