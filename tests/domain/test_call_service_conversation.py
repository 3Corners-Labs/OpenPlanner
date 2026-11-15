"""The call service creates a conversation task from the routing's kind (ADR 0123 step 7a).

The first message is composed at the edge in the organisation's language and handed in as a
function; the kind the task was created with is stored on the booking, whatever the routing says
later. A conversation is never built without its text and never for a platform that does not
carry it. And never for a booking whose service does not use the call page (ADR 0123 step 6b):
only that page can show the chat, so the platform switch is the kill switch and the service's own
client is the rule.
"""

from dataclasses import replace

import pytest

from app.domain.admin import AdminService
from app.domain.booking import MeetingRef
from app.domain.call_client import call_page_rule
from app.domain.call_service import CallService
from app.domain.calls import ContactCenterTask, EventKind, MeetingRequest, OpeningTextError
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef, TaskKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import IntegrationService
from app.domain.ports import IntegrationError
from app.domain.scheduling import JoinClient, Organisation, ScheduleRule
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
    join_client=JoinClient.EMBEDDED,
    video_host_email="host@example.nl",
    contact_center=webex_routing(
        "openplanner@example.biz", "openplanner-chat", task_kind=TaskKind.CONVERSATION
    ),
)
WEBEX_CC = ContactCenterKind.WEBEX_CC


def _opening(locale: str | None, task: ContactCenterTask) -> str:
    return f"{locale}: {task.reference} {task.first_name} {task.start_label} {task.meeting_link}"


async def _service(  # type: ignore[no-untyped-def]
    dispatcher: FakeDispatcher,
    *,
    opening=_opening,
    locale: str = "en",
    enabled: bool = True,
    group=CONVERSATION_GROUP,
):
    state = State()
    state.organisations[1] = Organisation(1, "Demo", "demo", default_locale=locale)
    state.groups[1] = group
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
        uses_page=call_page_rule(lambda: enabled),
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


async def test_a_conversation_is_in_force_only_while_the_switch_is_on() -> None:
    """The routing holds a conversation; with the switch off the task is a form, as before."""
    dispatcher = FakeDispatcher()
    uow, calls, booking = await _service(dispatcher, enabled=False)

    done = await calls.ensure_task(booking, CONVERSATION_GROUP)

    assert dispatcher.kinds == [TaskKind.FORM]
    [task] = dispatcher.items
    assert task.first_message == ""
    assert done.contact_center_task == ContactCenterTaskRef("task-1", WEBEX_CC, TaskKind.FORM)
    assert uow.state.bookings[booking.id or 0].contact_center_task == done.contact_center_task


async def test_the_opening_gets_no_language_when_the_organisation_cannot_be_read() -> None:
    seen: list[str | None] = []

    def opening(locale: str | None, task: ContactCenterTask) -> str:
        seen.append(locale)
        return "text"

    dispatcher = FakeDispatcher()
    uow, calls, booking = await _service(dispatcher, opening=opening, locale="en")
    uow.state.organisations.clear()

    await calls.ensure_task(booking, CONVERSATION_GROUP)

    assert seen == [None]  # the edge applies the installation's default


async def _held_task(dispatcher: FakeDispatcher, task_kind: TaskKind):  # type: ignore[no-untyped-def]
    uow, calls, booking = await _service(dispatcher)
    task = ContactCenterTaskRef("task-1", WEBEX_CC, task_kind)
    async with uow.locked() as tx:
        await tx.save_task(booking.id or 0, task)
    return calls, replace(booking, contact_center_task=task)


async def test_a_conversation_is_ended_without_the_work_items_cancellation_form() -> None:
    dispatcher = FakeDispatcher()
    calls, booking = await _held_task(dispatcher, TaskKind.CONVERSATION)

    await calls.end_task(booking, CONVERSATION_GROUP)

    assert dispatcher.marked == []
    assert dispatcher.ended == ["task-1"]


async def test_a_conversation_is_ended_even_when_a_mark_would_have_failed() -> None:
    """A task holding the meeting link is not left open because the form's mark fails."""
    dispatcher = FakeDispatcher()
    dispatcher.mark_error = IntegrationError("unreachable", retryable=True)
    calls, booking = await _held_task(dispatcher, TaskKind.CONVERSATION)

    await calls.end_task(booking, CONVERSATION_GROUP)

    assert dispatcher.ended == ["task-1"]


async def test_a_form_task_is_marked_and_then_ended_as_before() -> None:
    dispatcher = FakeDispatcher()
    calls, booking = await _held_task(dispatcher, TaskKind.FORM)

    await calls.end_task(booking, CONVERSATION_GROUP)

    assert [task_id for task_id, _ in dispatcher.marked] == ["task-1"]
    assert dispatcher.ended == ["task-1"]


async def test_a_form_task_whose_mark_fails_for_a_retry_is_not_ended_yet() -> None:
    """Behaviour of a form task is unchanged: a retryable mark failure raises, the job retries."""
    dispatcher = FakeDispatcher()
    dispatcher.mark_error = IntegrationError("unreachable", retryable=True)
    calls, booking = await _held_task(dispatcher, TaskKind.FORM)

    with pytest.raises(IntegrationError):
        await calls.end_task(booking, CONVERSATION_GROUP)

    assert dispatcher.ended == []


async def test_a_bad_opening_text_is_a_refusal_of_the_dispatch_not_an_error() -> None:
    def broken(locale: str | None, task: ContactCenterTask) -> str:
        raise OpeningTextError("a placeholder outside the list")

    dispatcher = FakeDispatcher()
    _, calls, booking = await _service(dispatcher, opening=broken)

    with pytest.raises(DomainError) as refused:
        await calls.ensure_task(booking, CONVERSATION_GROUP)

    assert refused.value.code is ErrorCode.INTEGRATION_NOT_READY
    assert refused.value.field == "opening_message"
    assert dispatcher.items == []


@pytest.mark.parametrize("client", [JoinClient.BROWSER, JoinClient.CHOICE])
async def test_a_service_that_does_not_use_the_call_page_never_gets_a_conversation(
    client: JoinClient,
) -> None:
    """The rule of ADR 0123 step 6b: the routing holds a conversation, the platform switch is on,
    and the booking still gets the work item, because its service's client is not ``embedded``."""
    group = replace(CONVERSATION_GROUP, join_client=client)
    dispatcher = FakeDispatcher()
    _, calls, booking = await _service(dispatcher, group=group)

    done = await calls.ensure_task(booking, group)

    assert dispatcher.kinds == [TaskKind.FORM]
    [task] = dispatcher.items
    assert task.first_message == ""
    assert done.contact_center_task == ContactCenterTaskRef("task-1", WEBEX_CC, TaskKind.FORM)


async def test_a_held_embedded_client_makes_no_conversation_while_the_switch_is_off() -> None:
    dispatcher = FakeDispatcher()
    _, calls, booking = await _service(dispatcher, enabled=False)

    await calls.ensure_task(booking, CONVERSATION_GROUP)

    assert dispatcher.kinds == [TaskKind.FORM]


@pytest.mark.parametrize(
    ("client", "enabled", "chat"),
    [
        (JoinClient.EMBEDDED, True, False),
        (JoinClient.EMBEDDED, False, True),  # a held choice while the switch is off: as before
        (JoinClient.BROWSER, True, True),
        (JoinClient.CHOICE, True, True),
    ],
)
async def test_the_meeting_is_created_without_the_platforms_chat_only_for_the_call_page(
    client: JoinClient, enabled: bool, chat: bool
) -> None:
    """ADR 0123 step 6b, behind the video port: the request says whether the chat is on."""
    group = replace(CONVERSATION_GROUP, join_client=client)
    dispatcher = FakeDispatcher()
    uow, calls, booking = await _service(dispatcher, group=group, enabled=enabled)
    video = FakeVideo()
    calls = CallService(
        uow,
        calls._integration,  # the service the helper built
        video,
        webex_cc(dispatcher),
        lambda b, g: "label",
        uses_page=call_page_rule(lambda: enabled),
    )

    await calls.ensure_meeting(replace(booking, meeting=None), group)

    assert [r.chat_enabled for r in video.created] == [chat]
    assert (video.created[0].title, video.created[0].host_email) == (
        "Videogesprek Balie",
        "host@example.nl",
    )


async def test_a_refused_meeting_without_chat_still_gives_the_participant_a_link() -> None:
    """ADR 0123 step 6b: the three chat options are refused (a 400): one retry with the chat on and
    a warning on the timeline with a code and nothing else, so the booking has its meeting."""
    dispatcher = FakeDispatcher()
    uow, calls, booking = await _service(dispatcher)
    refusing = FakeVideoRefusingNoChat()
    calls = CallService(
        uow,
        calls._integration,
        refusing,
        webex_cc(dispatcher),
        lambda b, g: "label",
        uses_page=call_page_rule(lambda: True),
    )

    done = await calls.ensure_meeting(replace(booking, meeting=None), CONVERSATION_GROUP)

    assert done.meeting is not None
    assert [r.chat_enabled for r in refusing.attempts] == [False, True]  # one retry, no more
    events = uow.state.booking_events[booking.id or 0]
    warning = [e for e in events if e.kind is EventKind.INTEGRATION_ERROR]
    assert [(e.detail, e.message) for e in warning] == [("chat_options_refused", "")]
    assert EventKind.MEETING_CREATED in [e.kind for e in events]


async def test_any_other_refusal_of_the_meeting_is_not_retried() -> None:
    dispatcher = FakeDispatcher()
    uow, calls, booking = await _service(dispatcher)
    other = FakeVideoRefusingNoChat(code="rejected_403")
    calls = CallService(
        uow,
        calls._integration,
        other,
        webex_cc(dispatcher),
        lambda b, g: "label",
        uses_page=call_page_rule(lambda: True),
    )

    with pytest.raises(IntegrationError):
        await calls.ensure_meeting(replace(booking, meeting=None), CONVERSATION_GROUP)

    assert len(other.attempts) == 1  # a 403 is not about the chat options
    assert EventKind.INTEGRATION_ERROR not in [
        e.kind for e in uow.state.booking_events.get(booking.id or 0, [])
    ]


async def test_a_meeting_with_the_chat_on_that_is_refused_is_not_retried() -> None:
    """A service that does not use the page asked for no change, so a 400 is a real refusal."""
    group = replace(CONVERSATION_GROUP, join_client=JoinClient.BROWSER)
    dispatcher = FakeDispatcher()
    uow, calls, booking = await _service(dispatcher, group=group)
    video = FakeVideoRefusingNoChat(refuse_all=True)
    calls = CallService(
        uow,
        calls._integration,
        video,
        webex_cc(dispatcher),
        lambda b, g: "label",
        uses_page=call_page_rule(lambda: True),
    )

    with pytest.raises(IntegrationError):
        await calls.ensure_meeting(replace(booking, meeting=None), group)

    assert len(video.attempts) == 1


class FakeVideoRefusingNoChat(FakeVideo):
    """A video fake that answers a 400 to a request without chat (or to every one)."""

    def __init__(self, code: str = "rejected_400", *, refuse_all: bool = False) -> None:
        super().__init__()
        self.attempts: list[MeetingRequest] = []
        self._code, self._all = code, refuse_all

    async def create_meeting(self, request: MeetingRequest) -> MeetingRef:
        self.attempts.append(request)
        if self._all or not request.chat_enabled:
            raise IntegrationError(self._code, retryable=False)
        return await super().create_meeting(request)
