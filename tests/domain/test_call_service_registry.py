"""The dispatcher is picked per platform (ADR 0032 point 5), like a sender per channel."""

from dataclasses import replace

from app.domain.admin import AdminService
from app.domain.booking import MeetingRef
from app.domain.call_service import CallService
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef
from app.domain.integration import IntegrationService
from app.domain.ports import ContactCenterDispatchers
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo, webex_cc
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, make_group, webex_routing, window

GROUP = make_group(
    id=1,
    slug="aangifte",
    name="Aangifte",
    video_host_email="host@example.nl",
    contact_center=webex_routing("openplanner@example.biz", "openplanner-video", "team-1"),
)

WEBEX_CC = ContactCenterKind.WEBEX_CC
TASK_1 = ContactCenterTaskRef("task-1", WEBEX_CC)


async def _service(dispatchers: ContactCenterDispatchers):  # type: ignore[no-untyped-def]
    state = State()
    state.groups[1] = GROUP
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    uow = InMemoryUnitOfWork(state)
    clock = FixedClock(NOW)
    receipt = await book(BookingService(uow, clock, TEST_TOKEN_KEY))
    booking = replace(receipt.booking, meeting=MeetingRef("m-1", "https://site.webex.com/m"))
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    calls = CallService(uow, integration, FakeVideo(), dispatchers, lambda b, g: "label")
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

    await calls.end_task(held)

    assert dispatcher.marked == [("t-9", held.reference)]
    assert dispatcher.ended == ["t-9"]


async def test_a_task_without_a_dispatcher_for_its_platform_is_left_alone() -> None:
    uow, calls, booking = await _service({})
    held = replace(booking, contact_center_task=ContactCenterTaskRef("t-9", WEBEX_CC))
    booking_id = booking.id or 0
    status_before = uow.state.call_status.get(booking_id)
    events_before = list(uow.state.booking_events.get(booking_id, []))

    await calls.end_task(held)  # nothing to call, nothing raised

    assert uow.state.booking_events.get(booking_id, []) == events_before  # no timeline event
    assert uow.state.call_status.get(booking_id) == status_before  # status unchanged
