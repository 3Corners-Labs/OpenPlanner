"""The chat's rows and what the service stores from a delivery (ADR 0123 step 7b).

The queries run on the real schema; the service decides per delivery (which booking, whether it
has a chat, whether it is a replay) over the same database. The purge test is the one that fails
if the booking's purge forgot the text.
"""

from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import BookingRow, CallMessageRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.call_chat import (
    CallMessage,
    ChatAuthor,
    ChatDirection,
    ChatEvent,
    ChatEventKind,
    ChatSender,
    ChatState,
)
from app.domain.call_chat_service import CallChatService
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef, TaskKind
from app.domain.integration import EventOutcome
from app.domain.purge import purge_booking
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, another_participant, book
from tests.support.seed_rows import seed_group

PROVIDER = ContactCenterKind.WEBEX_CC
SOURCE = "wxcc"


@dataclass
class Setup:
    """A confirmed booking with a conversation task, and the service that stores for it."""

    engine: AsyncEngine
    booking_id: int
    organisation_id: int
    service: CallChatService
    page: list[bool]
    """One element: whether the booking's service uses the call page (a test flips it)."""

    async def deliver(
        self, event: ChatEvent, key: str = "", organisation_id: int | None = None
    ) -> EventOutcome:
        return await self.service.record(
            event,
            NOW,
            provider=PROVIDER,
            source=SOURCE,
            organisation_id=self.organisation_id if organisation_id is None else organisation_id,
            event_key=key,
            scope="organisation:1",
            body_identity=f"body:{key}",
        )

    async def stored(self) -> list[CallMessage]:
        async with SqlUnitOfWork(self.engine).read() as tx:
            return await tx.list_call_messages(self.booking_id)


def _agent(text: str = "Goedemiddag", message_id: str = "m-1", task: str = "task-1") -> ChatEvent:
    return ChatEvent(
        ChatEventKind.APPENDED,
        task,
        ChatDirection.OUTBOUND,
        ChatSender.AGENT,
        message_id,
        text,
        NOW + timedelta(seconds=5),
    )


async def _make(engine: AsyncEngine, task_kind: TaskKind = TaskKind.CONVERSATION) -> Setup:
    await seed_group(engine)
    uow = SqlUnitOfWork(engine)
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0))
    booking_id = receipt.booking.id or 0
    async with uow.locked() as tx:
        await tx.save_task(booking_id, ContactCenterTaskRef("task-1", PROVIDER, task_kind))
        group = await tx.get_group_by_id(receipt.booking.group_id)
    assert group is not None
    page = [True]
    return Setup(
        engine, booking_id, group.organisation_id, CallChatService(uow, lambda _: page[0]), page
    )


@pytest.fixture
async def setup(engine: AsyncEngine) -> Setup:
    return await _make(engine)


# --- the queries ---------------------------------------------------------------------------------


def _message(booking_id: int, provider_id: str, **changes: object) -> CallMessage:
    base = CallMessage(
        booking_id, ChatAuthor.PARTICIPANT, "Hallo", NOW, ChatState.SENT, provider_id
    )
    return CallMessage(**{**base.__dict__, **changes})  # type: ignore[arg-type]


async def test_a_message_is_stored_once_per_provider_id_and_listed_oldest_first(
    setup: Setup,
) -> None:
    id_ = setup.booking_id
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        first = await tx.add_call_message(_message(id_, "b", at=NOW + timedelta(seconds=9)))
        again = await tx.add_call_message(_message(id_, "b", message_text="anders"))
        second = await tx.add_call_message(_message(id_, "a", at=NOW))

    stored = await setup.stored()

    assert (first, again, second) == (True, False, True)
    assert [m.provider_message_id for m in stored] == ["a", "b"]
    assert stored[1].message_text == "Hallo"  # the replay did not replace it
    assert all(m.id is not None for m in stored)


async def test_a_message_is_confirmed_once_and_a_refusal_closes_every_one_still_waiting(
    setup: Setup,
) -> None:
    id_ = setup.booking_id
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        for name in ("a", "b", "c"):
            await tx.add_call_message(_message(id_, name))
        confirmed = await tx.confirm_call_message(id_, "a")
        again = await tx.confirm_call_message(id_, "a")  # no longer ``sent``
        missing = await tx.confirm_call_message(id_, "zzz")
        refused = await tx.refuse_sent_call_messages(id_)

    states = {m.provider_message_id: m.state for m in await setup.stored()}

    assert (confirmed, again, missing, refused) == (True, False, False, 2)
    assert states == {
        "a": ChatState.DELIVERED,
        "b": ChatState.REFUSED,
        "c": ChatState.REFUSED,
    }


async def test_only_the_participants_messages_count_for_the_rate_and_only_since_the_moment(
    setup: Setup,
) -> None:
    id_ = setup.booking_id
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.add_call_message(_message(id_, "old", at=NOW - timedelta(minutes=5)))
        await tx.add_call_message(_message(id_, "new", at=NOW))
        await tx.add_call_message(_message(id_, "staff", at=NOW, author=ChatAuthor.STAFF))
    async with SqlUnitOfWork(setup.engine).read() as tx:
        times = await tx.participant_message_times(id_, NOW - timedelta(minutes=1))

    assert times == [NOW]


# --- the purge -----------------------------------------------------------------------------------


async def test_the_booking_purge_deletes_the_text_of_its_chat_and_only_its_own(
    engine: AsyncEngine, setup: Setup
) -> None:
    """ADR 0123 point 8: the text is deleted with the booking's purge like every other field."""
    other = await _another_booking(engine)
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.add_call_message(_message(setup.booking_id, "a", message_text="Mijn vraag"))
        await tx.add_call_message(_message(other, "a", message_text="Andermans vraag"))
        await purge_booking(tx, setup.booking_id, NOW + timedelta(days=2))

    async with engine.connect() as conn:
        left = (
            await conn.execute(select(CallMessageRow.booking_id, CallMessageRow.message_text))
        ).all()
        purged = (
            await conn.execute(
                select(BookingRow.pii_purged_at).where(BookingRow.id == setup.booking_id)
            )
        ).scalar_one()

    assert purged is not None
    assert [tuple(row) for row in left] == [(other, "Andermans vraag")]


async def _another_booking(engine: AsyncEngine) -> int:
    """Book a second appointment a week later: its chat must survive the first one's purge."""
    receipt = await book(
        BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0),
        start=SLOT_0900 + timedelta(days=7),
        contact=another_participant(),
    )
    return receipt.booking.id or 0


async def test_a_chat_row_cannot_outlive_its_booking(engine: AsyncEngine, setup: Setup) -> None:
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.add_call_message(_message(setup.booking_id, "a"))
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA foreign_keys = ON")
        await conn.exec_driver_sql(f"DELETE FROM bookings WHERE id = {setup.booking_id}")  # noqa: S608

    async with engine.connect() as conn:
        count = (await conn.execute(select(func.count()).select_from(CallMessageRow))).scalar_one()

    assert count == 0


# --- the service ---------------------------------------------------------------------------------


async def test_a_staff_message_is_stored_as_delivered_once_whatever_the_number_of_deliveries(
    setup: Setup,
) -> None:
    first = await setup.deliver(_agent(), key="k1")
    replay = await setup.deliver(_agent(), key="k1")  # the memory of the event
    again = await setup.deliver(_agent(), key="")  # no key: the provider id is the second guard

    stored = await setup.stored()

    assert (first, replay, again) == (
        EventOutcome.RECORDED,
        EventOutcome.DUPLICATE,
        EventOutcome.DUPLICATE,
    )
    assert [(m.author, m.state, m.message_text) for m in stored] == [
        (ChatAuthor.STAFF, ChatState.DELIVERED, "Goedemiddag")
    ]
    assert stored[0].at == NOW + timedelta(seconds=5)


async def test_a_message_without_a_time_gets_the_time_the_gate_used(setup: Setup) -> None:
    event = ChatEvent(
        ChatEventKind.APPENDED, "task-1", ChatDirection.OUTBOUND, ChatSender.AGENT, "m-9", "Hoi"
    )

    await setup.deliver(event, key="k")

    assert [m.at for m in await setup.stored()] == [NOW]


async def test_the_flows_own_text_is_not_stored_as_a_staff_message(setup: Setup) -> None:
    flow = ChatEvent(
        ChatEventKind.APPENDED,
        "task-1",
        ChatDirection.OUTBOUND,
        ChatSender.SYSTEM,
        "m-flow",
        "Please wait while we connect you with an agent.",
    )

    outcome = await setup.deliver(flow, key="k")

    assert outcome is EventOutcome.IGNORED
    assert await setup.stored() == []


async def test_the_confirmation_of_the_participants_message_marks_it_delivered(
    setup: Setup,
) -> None:
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.add_call_message(_message(setup.booking_id, "mine"))
    confirmation = ChatEvent(ChatEventKind.APPENDED, "task-1", message_id="mine")

    outcome = await setup.deliver(confirmation, key="c1")
    unknown = await setup.deliver(
        ChatEvent(ChatEventKind.APPENDED, "task-1", message_id="not-ours"), key="c2"
    )

    assert (outcome, unknown) == (EventOutcome.RECORDED, EventOutcome.IGNORED)
    assert [m.state for m in await setup.stored()] == [ChatState.DELIVERED]


async def test_a_refusal_after_the_end_closes_what_was_waiting(setup: Setup) -> None:
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.add_call_message(_message(setup.booking_id, "late"))

    outcome = await setup.deliver(ChatEvent(ChatEventKind.APPEND_FAILED, "task-1"), key="f")

    assert outcome is EventOutcome.RECORDED
    assert [m.state for m in await setup.stored()] == [ChatState.REFUSED]


async def test_a_task_of_another_organisation_or_no_task_at_all_is_unknown(setup: Setup) -> None:
    other_org = await setup.deliver(_agent(), key="k", organisation_id=setup.organisation_id + 1)
    no_task = await setup.deliver(_agent(task="not-a-task"), key="k2")

    assert (other_org, no_task) == (EventOutcome.UNKNOWN, EventOutcome.UNKNOWN)
    assert await setup.stored() == []


async def test_a_booking_without_a_chat_stores_nothing(engine: AsyncEngine) -> None:
    """A work item has no chat, and neither has a service that does not use the call page."""
    form = await _make(engine, TaskKind.FORM)
    assert await form.deliver(_agent(), key="k") is EventOutcome.IGNORED
    assert await form.stored() == []


async def test_switching_the_page_off_ends_the_chat_for_what_comes_in(setup: Setup) -> None:
    setup.page[0] = False

    outcome = await setup.deliver(_agent(), key="k")

    assert outcome is EventOutcome.IGNORED
    assert await setup.stored() == []


async def test_the_thread_is_read_oldest_first(setup: Setup) -> None:
    later = replace(_agent("Tweede", "m-2"), at=NOW + timedelta(seconds=9))
    await setup.deliver(later, key="a")
    await setup.deliver(_agent("Eerste", "m-1"), key="b")

    thread = await setup.service.thread(setup.booking_id)

    assert [m.message_text for m in thread] == ["Eerste", "Tweede"]
