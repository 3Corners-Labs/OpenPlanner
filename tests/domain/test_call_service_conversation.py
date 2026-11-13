"""The call service creates a conversation task from the routing's kind (ADR 0123 step 7a).

The first message is composed at the edge in the organisation's language and handed in as a
function; the kind the task was created with is stored on the booking, whatever the routing says
later. A conversation is never built without its text and never for a platform that does not
carry it.
"""

from dataclasses import replace

import pytest

from app.domain.admin import AdminService
from app.domain.booking import MeetingRef
from app.domain.call_service import CallService
from app.domain.calls import ContactCenterTask
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef, TaskKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import IntegrationService
from app.domain.scheduling import Organisation, ScheduleRule
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo, webex_cc
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, make_group, webex_routing, window

CONVERSATION_GROUP = make_group(
    id=1,
    organisation_id=1,
    slug="balie",
    name="Balie",
    video_host_email="host@example.nl",
    contact_center=webex_routing(
        "openplanner@example.biz", "openplanner-chat", task_kind=TaskKind.CONVERSATION
    ),
)
WEBEX_CC = ContactCenterKind.WEBEX_CC


def _opening(locale: str, task: ContactCenterTask) -> str:
    return f"{locale}: {task.reference} {task.first_name} {task.start_label} {task.meeting_link}"


async def _service(  # type: ignore[no-untyped-def]
    dispatcher: FakeDispatcher, *, opening=_opening, locale: str = "en"
):
    state = State()
    state.organisations[1] = Organisation(1, "Demo", "demo", default_locale=locale)
    state.groups[1] = CONVERSATION_GROUP
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    uow = InMemoryUnitOfWork(state)
    clock = FixedClock(NOW)
    receipt = await book(BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0))
    booking = replace(receipt.booking, meeting=MeetingRef("m-1", "https://site.webex.com/m"))
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    calls = CallService(
        uow,
        integration,
        FakeVideo(),
        webex_cc(dispatcher),
        lambda b, g: "label",
        opening=opening,
    )
    return uow, calls, booking


async def test_a_conversation_is_created_with_its_first_message_in_the_organisations_language() -> (
    None
):
    dispatcher = FakeDispatcher()
    uow, calls, booking = await _service(dispatcher, locale="en")

    done = await calls.ensure_task(booking, CONVERSATION_GROUP)

    [task] = dispatcher.items
    assert task.task_kind is TaskKind.CONVERSATION
    assert task.first_message.startswith("en: ")
    assert booking.contact is not None
    assert booking.contact.first_name in task.first_message
    assert dispatcher.kinds == [TaskKind.CONVERSATION]
    assert done.contact_center_task == ContactCenterTaskRef(
        "task-1", WEBEX_CC, TaskKind.CONVERSATION
    )
    assert uow.state.bookings[booking.id or 0].contact_center_task == done.contact_center_task


async def test_a_task_keeps_the_kind_it_was_created_with() -> None:
    dispatcher = FakeDispatcher()
    uow, calls, booking = await _service(dispatcher)
    done = await calls.ensure_task(booking, CONVERSATION_GROUP)

    uow.state.groups[1] = replace(
        CONVERSATION_GROUP, contact_center=webex_routing("openplanner@example.biz", "chat")
    )

    stored = uow.state.bookings[booking.id or 0].contact_center_task
    assert stored is not None
    assert stored.task_kind is TaskKind.CONVERSATION
    assert done.contact_center_task == stored


async def test_a_conversation_cannot_be_built_without_the_opening_message() -> None:
    dispatcher = FakeDispatcher()
    _, calls, booking = await _service(dispatcher, opening=None)

    with pytest.raises(DomainError) as refused:
        await calls.ensure_task(booking, CONVERSATION_GROUP)

    assert refused.value.code is ErrorCode.INTEGRATION_NOT_READY
    assert refused.value.field == "opening_message"
    assert dispatcher.items == []  # nothing was sent, and no work item instead


async def test_a_work_item_needs_no_opening_message() -> None:
    dispatcher = FakeDispatcher()
    _, calls, booking = await _service(dispatcher, opening=None)
    group = replace(
        CONVERSATION_GROUP,
        contact_center=webex_routing("openplanner@example.biz", "openplanner-video"),
    )

    done = await calls.ensure_task(booking, group)

    assert dispatcher.kinds == [TaskKind.FORM]
    assert done.contact_center_task == ContactCenterTaskRef("task-1", WEBEX_CC, TaskKind.FORM)
