"""The chat's rows and what the service stores from a delivery (ADR 0123 step 7b).

The queries run on the real schema; the service decides per delivery (which booking, whether it
has a chat, whether it is a replay) over the same database. The purge test is the one that fails
if the booking's purge forgot the text.
"""

from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import AuditEventRow, BookingRow, CallMessageRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.call_chat import (
    MAX_MESSAGES_PER_BOOKING,
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
from app.domain.contact_center_dispatch import DispatcherChoice
from app.domain.integration import EventOutcome
from app.domain.purge import purge_booking
from app.domain.retention_sweep import RetentionSweep
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.support.domain import (
    NOW,
    SLOT_0900,
    SLOT_0930,
    TEST_TOKEN_KEY,
    another_participant,
    book,
)
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
        self,
        event: ChatEvent,
        key: str = "",
        organisation_id: int | None = None,
        body: str | None = None,
    ) -> EventOutcome:
        return await self.service.record(
            event,
            NOW,
            provider=PROVIDER,
            source=SOURCE,
            organisation_id=self.organisation_id if organisation_id is None else organisation_id,
            event_key=key,
            scope="organisation:1",
            body_identity=body if body is not None else f"body:{key}",
        )

    async def stored(self) -> list[CallMessage]:
        async with SqlUnitOfWork(self.engine).read() as tx:
            return await tx.messages_of_call(self.booking_id)


def _agent(text: str = "Goedemiddag", message_id: str = "m-1", task: str = "task-1") -> ChatEvent:
    return ChatEvent(
        ChatEventKind.APPENDED,
        task,
        ChatDirection.OUTBOUND,
        ChatSender.AGENT,
        message_id,
        text,
        NOW - timedelta(seconds=5),
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
        engine,
        booking_id,
        group.organisation_id,
        CallChatService(uow, lambda _: page[0], lambda: DispatcherChoice({}), FixedClock(NOW)),
        page,
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


async def test_a_message_is_stored_once_per_provider_id_and_listed_in_arrival_order(
    setup: Setup,
) -> None:
    id_ = setup.booking_id
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        first = await tx.add_call_message(_message(id_, "b", at=NOW + timedelta(seconds=9)))
        again = await tx.add_call_message(_message(id_, "b", message_text="anders"))
        second = await tx.add_call_message(_message(id_, "a", at=NOW))

    stored = await setup.stored()

    assert (first, again, second) == (True, False, True)
    # By id, the order the messages arrived in, whatever time each carries: the live page appends
    # in that order, so a reload shows the same.
    assert [m.provider_message_id for m in stored] == ["b", "a"]
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
    assert stored[0].at == NOW - timedelta(seconds=5)


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


async def test_the_thread_is_read_in_arrival_order_not_in_the_platforms_time(
    setup: Setup,
) -> None:
    later = replace(_agent("Tweede", "m-2"), at=NOW - timedelta(seconds=1))
    await setup.deliver(later, key="a")
    await setup.deliver(_agent("Eerste", "m-1"), key="b")

    thread = await setup.service.thread(setup.booking_id)

    assert [m.message_text for m in thread] == ["Tweede", "Eerste"]


# --- the bound, the closed booking, the clock, a confirmation that comes early -----------------


async def _audit_actions(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        return list((await conn.execute(select(AuditEventRow.action))).scalars())


async def test_a_chat_holds_at_most_the_fixed_number_of_messages_and_says_so_once(
    setup: Setup,
) -> None:
    """The gate limits requests, not rows: a secret holder cannot grow the table without end."""
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        for number in range(MAX_MESSAGES_PER_BOOKING - 1):
            await tx.add_call_message(_message(setup.booking_id, f"seed-{number}"))

    last = await setup.deliver(_agent(message_id="the-500th"), key="k500")
    over = await setup.deliver(_agent(message_id="the-501st"), key="k501")
    more = await setup.deliver(_agent(message_id="the-502nd"), key="k502")

    async with SqlUnitOfWork(setup.engine).read() as tx:
        held = await tx.count_call_messages(setup.booking_id)
    assert (last, over, more) == (EventOutcome.RECORDED, EventOutcome.IGNORED, EventOutcome.IGNORED)
    assert held == MAX_MESSAGES_PER_BOOKING == 500
    assert (await _audit_actions(setup.engine)).count("chat.limit_reached") == 1


async def test_a_cancelled_booking_takes_no_more_messages(setup: Setup) -> None:
    async with setup.engine.begin() as conn:
        await conn.execute(
            update(BookingRow).where(BookingRow.id == setup.booking_id).values(status="cancelled")
        )

    outcome = await setup.deliver(_agent(), key="k")

    assert outcome is EventOutcome.IGNORED
    assert await setup.stored() == []


async def test_a_message_cannot_claim_a_time_after_the_gates_own(setup: Setup) -> None:
    future = replace(_agent(), at=NOW + timedelta(days=3))
    past = replace(_agent(message_id="m-past"), at=NOW - timedelta(minutes=2))

    await setup.deliver(future, key="a")
    await setup.deliver(past, key="b")

    assert [m.at for m in await setup.stored()] == [NOW, NOW - timedelta(minutes=2)]


async def test_a_confirmation_that_comes_before_its_message_is_processed_when_it_comes_again(
    setup: Setup,
) -> None:
    """The row is committed after the request returns: an early confirmation is no replay."""
    confirmation = ChatEvent(ChatEventKind.APPENDED, "task-1", message_id="mine")

    early = await setup.deliver(confirmation, key="same-event")
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.add_call_message(_message(setup.booking_id, "mine"))
    late = await setup.deliver(confirmation, key="same-event")
    again = await setup.deliver(confirmation, key="same-event")

    assert (early, late, again) == (
        EventOutcome.IGNORED,
        EventOutcome.RECORDED,
        EventOutcome.IGNORED,
    )
    assert [m.state for m in await setup.stored()] == [ChatState.DELIVERED]


async def test_a_refusal_with_nothing_waiting_is_not_remembered_and_two_bodies_are_two_refusals(
    setup: Setup,
) -> None:
    """Without a message id a refusal is told apart by its signed body, which carries its time."""
    failed = ChatEvent(ChatEventKind.APPEND_FAILED, "task-1")

    nothing = await setup.deliver(failed, key="", body="body:t1")
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.add_call_message(_message(setup.booking_id, "first"))
    first = await setup.deliver(failed, key="", body="body:t1")  # now there is something
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.add_call_message(_message(setup.booking_id, "second"))
    replay = await setup.deliver(failed, key="", body="body:t1")  # the same signed body
    second = await setup.deliver(failed, key="", body="body:t2")  # another second, another body

    states = {m.provider_message_id: m.state for m in await setup.stored()}
    assert (nothing, first, replay, second) == (
        EventOutcome.IGNORED,
        EventOutcome.RECORDED,
        EventOutcome.DUPLICATE,
        EventOutcome.RECORDED,
    )
    assert states == {"first": ChatState.REFUSED, "second": ChatState.REFUSED}


async def test_the_nightly_sweep_deletes_the_chat_of_a_booking_the_purge_job_missed(
    setup: Setup,
) -> None:
    """The safety net under the purge job reaches the same delete (threat model, retention)."""
    async with SqlUnitOfWork(setup.engine).locked() as tx:
        await tx.add_call_message(_message(setup.booking_id, "a", message_text="Mijn vraag"))
    clock = FixedClock(SLOT_0930 + timedelta(hours=26))

    result = await RetentionSweep(SqlUnitOfWork(setup.engine), clock).run()

    assert (result.overdue, result.purged) == (1, 1)
    assert await setup.stored() == []
