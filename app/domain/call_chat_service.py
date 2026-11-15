"""Storing what the contact center delivers of a conversation, and reading the thread (ADR 0123).

A verified delivery of the platform (through the gate of ADR 0124) is matched to the booking that
owns the task and to nothing else, and written in one locked transaction with the memory of its
event, so a replay changes nothing the second time:

    ChatEvent ─► booking by task id (at that platform, in that organisation's bookings only)
              ─► does the booking have a chat? (``has_chat``: a conversation task, a service that
                 uses the call page) ─► no: ignored, nothing stored
              ─► replay? ─► yes: duplicate
              ─► appended, outbound, from a staff member ─► stored as a staff message, delivered
                 appended, inbound                       ─► our ``sent`` message is ``delivered``
                 appended, outbound, from the flow       ─► ignored: the flow's text is not the
                                                            staff member's (the sample's queue
                                                            text, in the platform's language)
                 append-failed                           ─► every ``sent`` message is ``refused``

Nothing of a delivery is kept but the message's text, its identifier, its time and its direction:
no sender identifier, no name, no origin. The text is personal data (``privacy.CHAT_FIELDS``) and
never reaches a log, an audit event or a job payload from here; the booking's purge deletes it.

The participant's own messages (the side bar sending) and the page's reading of the thread are
step 7c; ``thread`` and the rules in ``app.domain.call_chat`` are what it uses (it applies them;
nothing calls ``thread`` in step 7b but its test).
"""

from datetime import datetime

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.booking import BookingStatus
from app.domain.call_chat import (
    MAX_MESSAGES_PER_BOOKING,
    CallMessage,
    ChatAuthor,
    ChatDirection,
    ChatEvent,
    ChatEventKind,
    ChatSender,
    ChatState,
    has_chat,
)
from app.domain.call_client import UsesCallPage
from app.domain.contact_center import ContactCenterKind
from app.domain.integration import EventOutcome, claim_event
from app.domain.ports import Transaction, UnitOfWork

CLOSED_STATUSES = frozenset({BookingStatus.CANCELLED, BookingStatus.REFUSED})
"""Booking states whose chat takes nothing more: the appointment is not going ahead."""
LIMIT_EVENT = "chat.limit_reached"
"""The system event written once when a booking's chat reaches ``MAX_MESSAGES_PER_BOOKING``."""


class CallChatService:
    """Stores the deliveries of a conversation and reads a booking's thread.

    Args:
        uow: Opens database transactions.
        uses_call_page: Whether a service's participant gets the call page now
            (``app.domain.call_client.call_page_rule``); a booking without it has no chat.
    """

    def __init__(self, uow: UnitOfWork, uses_call_page: UsesCallPage) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._uses_page = uses_call_page

    async def record(
        self,
        event: ChatEvent,
        at: datetime,
        *,
        provider: ContactCenterKind,
        source: str,
        organisation_id: int,
        event_key: str,
        scope: str,
        body_identity: str,
    ) -> EventOutcome:
        """Record one verified chat event once; ``UNKNOWN`` for a task that is not this one's.

        The task is looked up at ``provider`` only and in ``organisation_id``'s bookings only
        (ADR 0124 point 12): a task of another organisation, or at another platform, is unknown,
        which the answer to the platform never tells apart from any other unknown task.

        Args:
            event: What the delivery says.
            at: The time the gate used; a message without a time of its own is stamped with it.
            provider: The platform the delivery came from.
            source: The platform's code under which its event keys are remembered.
            organisation_id: The organisation whose own address it came in at.
            event_key: The event's digest for the replay memory (empty: use the body's).
            scope: The memory's scope of the address.
            body_identity: The identity of the verified body, when the event has no key.

        Returns:
            ``RECORDED``, ``DUPLICATE`` (a replay), ``IGNORED`` (a booking without a chat or a
            cancelled one, a message from the flow, a full chat, or a confirmation or refusal
            that finds nothing waiting: not remembered, so its redelivery counts) or
            ``UNKNOWN``.
        """
        async with self._uow.locked() as tx:
            booking = await tx.get_booking_by_task(event.task_id, provider)
            if booking is None or booking.id is None:
                return EventOutcome.UNKNOWN
            group = await tx.get_group_by_id(booking.group_id)
            if group is None or group.organisation_id != organisation_id:
                return EventOutcome.UNKNOWN
            task = booking.contact_center_task
            if not has_chat(task.task_kind if task else None, self._uses_page(group)):
                return EventOutcome.IGNORED
            if booking.status in CLOSED_STATUSES or not _worth_keeping(event):
                return EventOutcome.IGNORED
            # Asked before the replay memory is touched: an event that changes nothing now (a
            # confirmation whose message is not stored yet) must stay unclaimed, so its redelivery
            # is processed when the message exists.
            held = await tx.count_call_messages(booking.id)
            waiting = await tx.sent_call_message_ids(booking.id)
            if not _would_change(event, held, waiting):
                return EventOutcome.IGNORED
            if not await claim_event(tx, source, event_key, scope, body_identity, at):
                return EventOutcome.DUPLICATE
            return await _apply(tx, booking.id, event, at, held)

    async def thread(self, booking_id: int) -> list[CallMessage]:
        """Return a booking's chat, oldest first.

        The caller has checked the participant's session for the booking and that the booking has
        a chat (``has_chat``); nothing here looks at either.
        """
        async with self._uow.read() as tx:
            return await tx.messages_of_call(booking_id)


def _would_change(event: ChatEvent, held: int, waiting: list[str]) -> bool:
    """Return whether ``event`` would change the thread now.

    A refusal needs a message waiting, a confirmation the very message it names, and a staff
    message room under ``MAX_MESSAGES_PER_BOOKING``.
    """
    if event.kind is ChatEventKind.APPEND_FAILED:
        return bool(waiting)
    if event.direction is ChatDirection.INBOUND:
        return event.message_id in waiting
    return held < MAX_MESSAGES_PER_BOOKING


async def _apply(
    tx: Transaction, booking_id: int, event: ChatEvent, at: datetime, held: int
) -> EventOutcome:
    """Write what ``event`` says to the booking's thread, in the caller's transaction.

    A staff message is stamped with the time it claims, never later than ``at`` (the gate's time):
    a signed body cannot put a message in the future. The message that fills the chat leaves one
    system event ``chat.limit_reached`` (a code, no text).
    """
    if event.kind is ChatEventKind.APPEND_FAILED:
        await tx.refuse_sent_call_messages(booking_id)
        return EventOutcome.RECORDED
    if event.direction is ChatDirection.INBOUND:
        await tx.confirm_call_message(booking_id, event.message_id)
        return EventOutcome.RECORDED
    stored = await tx.add_call_message(
        CallMessage(
            booking_id,
            ChatAuthor.STAFF,
            event.message_text,
            min(event.at or at, at),
            ChatState.DELIVERED,
            event.message_id,
        )
    )
    if stored and held + 1 >= MAX_MESSAGES_PER_BOOKING:
        await tx.add_audit_event(
            AuditEvent(
                at,
                SYSTEM_ACTOR,
                LIMIT_EVENT,
                Outcome.FAILURE,
                "limit_reached",
                None,
                None,
                "",
                Source.SYSTEM,
                booking_id=booking_id,
            )
        )
    return EventOutcome.RECORDED if stored else EventOutcome.DUPLICATE


def _worth_keeping(event: ChatEvent) -> bool:
    """Return whether an event changes the thread: not an outbound message of the flow."""
    return not (
        event.kind is ChatEventKind.APPENDED
        and event.direction is ChatDirection.OUTBOUND
        and event.sender is not ChatSender.AGENT
    )
