"""What the participant's side of the chat does over the real schema (ADR 0123 step 7c).

``CallChatService.send`` stores a message ``sent`` before it hands it to the contact center and
takes it back when the platform cannot be reached; ``read`` returns what is new, the new state of
the messages the page still shows as waiting, and why the chat is closed. The fake dispatcher is
the contact center. The tests that would fail if a rule were wrong: the same identifier twice is
one message, nothing is kept that never left, a closed or full chat takes nothing, the rate counts
the participant's own messages only.
"""

from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import Booking, BookingStatus
from app.domain.call_chat import (
    MAX_MESSAGES_PER_BOOKING,
    CallMessage,
    ChatAuthor,
    ChatClosed,
    ChatDirection,
    ChatEvent,
    ChatEventKind,
    ChatState,
)
from app.domain.call_chat_service import WAITING_CAP, CallChatService, SendRefusal
from app.domain.calls import CallStatus, TaskState
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef, TaskKind
from app.domain.contact_center_dispatch import DispatcherChoice
from app.domain.integration import EventOutcome
from app.domain.ports import IntegrationError
from app.domain.scheduling import Group
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, webex_cc
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, book
from tests.support.seed_rows import seed_group

PROVIDER = ContactCenterKind.WEBEX_CC
ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
OTHER_ID = "0f9e8d7c6b5a49382716f5e4d3c2b1a0"
WORDS = "Ik heb een vraag over mijn afspraak"


@dataclass
class Setup:
    """A confirmed booking with a conversation task, the service and the fake contact center."""

    engine: AsyncEngine
    booking_id: int
    organisation_id: int
    service: CallChatService
    dispatcher: FakeDispatcher
    clock: FixedClock
    page: list[bool]

    async def booking(self) -> tuple[Booking, Group]:
        async with SqlUnitOfWork(self.engine).read() as tx:
            booking = await tx.get_booking(self.booking_id)
            assert booking is not None
            group = await tx.get_group_by_id(booking.group_id)
        assert group is not None
        return booking, group

    async def send(
        self, text: str = WORDS, client_id: str = ID, *, per_minute: int = 20, max_chars: int = 1000
    ) -> CallMessage | SendRefusal:
        booking, group = await self.booking()
        return await self.service.send(
            booking, group, text, client_id, max_chars=max_chars, per_minute=per_minute
        )

    async def read(self, since: int = 0, waiting: list[int] | None = None):  # noqa: ANN201
        booking, group = await self.booking()
        return await self.service.read(booking, group, since, waiting or [])

    async def stored(self) -> list[CallMessage]:
        async with SqlUnitOfWork(self.engine).read() as tx:
            return await tx.messages_of_call(self.booking_id)

    async def put(self, message: CallMessage) -> None:
        async with SqlUnitOfWork(self.engine).locked() as tx:
            await tx.add_call_message(message)

    async def deliver(self, event: ChatEvent, key: str) -> EventOutcome:
        return await self.service.record(
            event,
            NOW,
            provider=PROVIDER,
            source="wxcc",
            organisation_id=self.organisation_id,
            event_key=key,
            scope="organisation:1",
            body_identity=f"body:{key}",
        )


async def _make(engine: AsyncEngine, task_kind: TaskKind = TaskKind.CONVERSATION) -> Setup:
    await seed_group(engine)
    uow = SqlUnitOfWork(engine)
    clock = FixedClock(NOW)
    receipt = await book(
        BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0), start=SLOT_0900
    )
    booking_id = receipt.booking.id or 0
    async with uow.locked() as tx:
        await tx.save_task(booking_id, ContactCenterTaskRef("task-1", PROVIDER, task_kind))
        group = await tx.get_group_by_id(receipt.booking.group_id)
    assert group is not None
    dispatcher = FakeDispatcher()
    page = [True]
    service = CallChatService(
        uow,
        lambda _: page[0],
        lambda: DispatcherChoice(webex_cc(dispatcher)),
        clock,
    )
    return Setup(engine, booking_id, group.organisation_id, service, dispatcher, clock, page)


@pytest.fixture
async def setup(engine: AsyncEngine) -> Setup:
    return await _make(engine)


def _mine(booking_id: int, provider_id: str, at_offset: int = 0, **changes: object) -> CallMessage:
    base = CallMessage(
        booking_id,
        ChatAuthor.PARTICIPANT,
        "eerder",
        NOW + timedelta(seconds=at_offset),
        ChatState.SENT,
        provider_id,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


# --- sending -------------------------------------------------------------------------------------


async def test_a_message_is_stored_sent_and_appended_to_the_task_under_its_own_identifier(
    setup: Setup,
) -> None:
    outcome = await setup.send()

    assert isinstance(outcome, CallMessage)
    assert (outcome.state, outcome.author, outcome.message_text) == (
        ChatState.SENT,
        ChatAuthor.PARTICIPANT,
        WORDS,
    )
    assert outcome.id is not None
    assert [(task, m.message_id, m.text) for task, m in setup.dispatcher.appended] == [
        ("task-1", f"p{ID}", WORDS)
    ]
    assert [m.provider_message_id for m in await setup.stored()] == [f"p{ID}"]


async def test_the_same_identifier_twice_is_one_message_and_is_not_appended_again(
    setup: Setup,
) -> None:
    first = await setup.send()
    again = await setup.send()

    assert isinstance(first, CallMessage)
    assert isinstance(again, CallMessage)
    assert again.id == first.id
    assert len(await setup.stored()) == 1
    assert len(setup.dispatcher.appended) == 1


async def test_control_and_direction_characters_are_taken_out_before_anything_is_kept(
    setup: Setup,
) -> None:
    outcome = await setup.send("Hal\x00lo‮ dag\r\nregel twee\x07")

    assert isinstance(outcome, CallMessage)
    assert outcome.message_text == "Hallo dag\nregel twee"
    assert setup.dispatcher.appended[0][1].text == "Hallo dag\nregel twee"


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        ("", SendRefusal.EMPTY),
        ("  \n\t ", SendRefusal.EMPTY),
        ("\x00\x01", SendRefusal.EMPTY),  # nothing is left after the cleaning
        ("a" * 201, SendRefusal.TOO_LONG),
    ],
)
async def test_an_empty_or_too_long_message_is_refused_and_nothing_is_kept(
    setup: Setup, text: str, refusal: SendRefusal
) -> None:
    assert await setup.send(text, max_chars=200) is refusal
    assert await setup.stored() == []
    assert setup.dispatcher.appended == []


@pytest.mark.parametrize(
    "bad", ["", "short", "x" * 41, "has space 0123456789abcdef", "ünicode0123456789abcdef"]
)
async def test_an_identifier_that_the_page_would_not_make_is_refused(
    setup: Setup, bad: str
) -> None:
    assert await setup.send(client_id=bad) is SendRefusal.BAD_REQUEST
    assert setup.dispatcher.appended == []


async def test_the_rate_counts_the_participants_own_messages_in_the_last_minute(
    setup: Setup,
) -> None:
    assert isinstance(await setup.send(client_id="a" * 16, per_minute=2), CallMessage)
    assert isinstance(await setup.send(client_id="b" * 16, per_minute=2), CallMessage)

    assert await setup.send(client_id="c" * 16, per_minute=2) is SendRefusal.RATE_LIMITED

    setup.clock.advance(timedelta(minutes=1, seconds=1))
    assert isinstance(await setup.send(client_id="c" * 16, per_minute=2), CallMessage)


async def test_staff_messages_do_not_use_up_the_participants_rate(setup: Setup) -> None:
    for number in range(3):
        await setup.put(
            _mine(
                setup.booking_id,
                f"staff-{number}",
                author=ChatAuthor.STAFF,
                state=ChatState.DELIVERED,
            )
        )

    assert isinstance(await setup.send(per_minute=1), CallMessage)


async def test_the_ceiling_of_a_booking_takes_no_more(setup: Setup) -> None:
    for number in range(MAX_MESSAGES_PER_BOOKING):
        await setup.put(
            _mine(
                setup.booking_id,
                f"old-{number}",
                author=ChatAuthor.STAFF,
                state=ChatState.DELIVERED,
            )
        )

    assert await setup.send() is SendRefusal.FULL
    assert setup.dispatcher.appended == []


async def test_a_platform_that_cannot_be_reached_keeps_nothing_and_the_retry_is_the_same_message(
    setup: Setup,
) -> None:
    setup.dispatcher.append_error = IntegrationError("unreachable", retryable=True)

    assert await setup.send() is SendRefusal.UNAVAILABLE
    assert await setup.stored() == []

    setup.dispatcher.append_error = None
    outcome = await setup.send()

    assert isinstance(outcome, CallMessage)
    assert [m.message_id for _, m in setup.dispatcher.appended] == [f"p{ID}"]


async def test_without_a_dispatcher_for_the_platform_nothing_is_kept(engine: AsyncEngine) -> None:
    made = await _make(engine)
    uow = SqlUnitOfWork(engine)
    service = CallChatService(uow, lambda _: True, lambda: DispatcherChoice({}), made.clock)
    booking, group = await made.booking()

    outcome = await service.send(booking, group, WORDS, ID, max_chars=1000, per_minute=20)

    assert outcome is SendRefusal.UNAVAILABLE
    assert await made.stored() == []


async def test_a_booking_without_a_chat_takes_nothing(engine: AsyncEngine) -> None:
    form = await _make(engine, TaskKind.FORM)

    assert await form.send() is SendRefusal.NO_CHAT
    assert form.dispatcher.appended == []


async def test_a_service_that_does_not_use_the_page_takes_nothing(setup: Setup) -> None:
    setup.page[0] = False

    assert await setup.send() is SendRefusal.NO_CHAT
    assert await setup.read() is None


async def test_a_cancelled_booking_takes_nothing(setup: Setup) -> None:
    booking, _ = await setup.booking()
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.save_booking(replace(booking, status=BookingStatus.CANCELLED))

    assert await setup.send() is SendRefusal.CLOSED
    assert setup.dispatcher.appended == []


@pytest.mark.parametrize("task", [TaskState.ENDED, TaskState.FAILED, TaskState.CANCELLED])
async def test_a_task_that_ended_closes_the_chat(setup: Setup, task: TaskState) -> None:
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.save_call_status(CallStatus(setup.booking_id, task=task))

    assert await setup.send() is SendRefusal.CLOSED
    read = await setup.read()
    assert read is not None
    assert read.closed is ChatClosed.STAFF


async def test_a_refused_message_closes_the_chat(setup: Setup) -> None:
    await setup.put(_mine(setup.booking_id, "pold", state=ChatState.REFUSED))

    assert await setup.send() is SendRefusal.CLOSED


async def test_a_service_built_without_a_dispatcher_source_says_so(engine: AsyncEngine) -> None:
    from app.domain.errors import DomainError

    made = await _make(engine)
    bare = CallChatService(SqlUnitOfWork(engine), lambda _: True)
    booking, group = await made.booking()

    with pytest.raises(DomainError):
        await bare.send(booking, group, WORDS, ID, max_chars=1000, per_minute=20)


# --- the confirmation closes the loop ------------------------------------------------------------


async def test_the_platforms_confirmation_makes_the_sent_message_delivered(setup: Setup) -> None:
    sent = await setup.send()
    assert isinstance(sent, CallMessage)
    event = ChatEvent(ChatEventKind.APPENDED, "task-1", ChatDirection.INBOUND, message_id=f"p{ID}")

    assert await setup.deliver(event, "k1") is EventOutcome.RECORDED

    assert [m.state for m in await setup.stored()] == [ChatState.DELIVERED]
    read = await setup.read(since=sent.id or 0, waiting=[sent.id or 0])
    assert read is not None
    assert read.states == {sent.id: ChatState.DELIVERED}


# --- reading -------------------------------------------------------------------------------------


async def test_a_read_returns_only_what_is_newer_than_the_cursor(setup: Setup) -> None:
    first = await setup.send(client_id="a" * 16)
    second = await setup.send(client_id="b" * 16)
    assert isinstance(first, CallMessage)
    assert isinstance(second, CallMessage)

    everything = await setup.read()
    after_first = await setup.read(since=first.id or 0)
    nothing = await setup.read(since=second.id or 0)

    assert everything is not None
    assert after_first is not None
    assert nothing is not None
    assert [m.id for m in everything.messages] == [first.id, second.id]
    assert [m.id for m in after_first.messages] == [second.id]
    assert nothing.messages == []
    assert nothing.states == {}
    assert nothing.closed is None


async def test_a_read_names_the_state_of_the_waiting_messages_asked_for_only(setup: Setup) -> None:
    waiting = await setup.send(client_id="a" * 16)
    refused = await setup.send(client_id="b" * 16)
    assert isinstance(waiting, CallMessage)
    assert isinstance(refused, CallMessage)
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.refuse_sent_call_messages(setup.booking_id)

    read = await setup.read(since=refused.id or 0, waiting=[waiting.id or 0])

    assert read is not None
    assert read.states == {waiting.id: ChatState.REFUSED}  # not the one the page did not name
    assert read.closed is ChatClosed.STAFF


async def test_a_read_of_a_still_waiting_message_says_nothing_about_it(setup: Setup) -> None:
    waiting = await setup.send()
    assert isinstance(waiting, CallMessage)

    read = await setup.read(since=waiting.id or 0, waiting=[waiting.id or 0])

    assert read is not None
    assert read.states == {}


async def test_a_booking_that_is_over_reads_as_ended(setup: Setup) -> None:
    booking, _ = await setup.booking()
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.save_booking(replace(booking, status=BookingStatus.COMPLETED))

    read = await setup.read()

    assert read is not None
    assert read.closed is ChatClosed.ENDED


async def test_the_list_of_waiting_ids_is_cut(setup: Setup) -> None:
    for number in range(WAITING_CAP + 5):
        await setup.put(_mine(setup.booking_id, f"p{number}", number, state=ChatState.DELIVERED))
    stored = await setup.stored()

    read = await setup.read(since=stored[-1].id or 0, waiting=[m.id or 0 for m in stored])

    assert read is not None
    assert len(read.states) == WAITING_CAP
