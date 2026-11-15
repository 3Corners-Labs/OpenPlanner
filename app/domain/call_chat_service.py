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

The participant's side (step 7c) is ``read`` and ``send``:

    send(booking, text, id) ─► the chat exists, is open, the text and the rate are in bounds, room
                               is left ─► stored ``sent`` (committed) ─► appended to the contact
                               center's task, its identifier the message's ``aliasId`` ─► the
                               platform's confirmation (above) makes it ``delivered``; when the
                               platform cannot be reached the stored message is taken back, so
                               nothing stays that never left
    read(booking, since, waiting) ─► what is new since the page's last look, the new state of the
                               messages it still shows as waiting, and whether the chat is closed

Both are asked after the route has checked the platform switch, the participant's session and the
booking's service; the service checks that the booking has a chat at all.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.booking import Booking, BookingStatus
from app.domain.call_chat import (
    CLIENT_ID,
    MAX_MESSAGES_PER_BOOKING,
    PROVIDER_PREFIX,
    CallMessage,
    ChatAuthor,
    ChatClosed,
    ChatDirection,
    ChatEvent,
    ChatEventKind,
    ChatRefusal,
    ChatSender,
    ChatState,
    chat_closed,
    check_text,
    clean_text,
    has_chat,
    within_rate,
)
from app.domain.call_client import UsesCallPage
from app.domain.calls import ParticipantMessage, TaskState
from app.domain.contact_center import ContactCenterKind
from app.domain.contact_center_dispatch import DispatcherChoice
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import EventOutcome, claim_event
from app.domain.ports import Clock, IntegrationError, Transaction, UnitOfWork
from app.domain.scheduling import Group

CLOSED_STATUSES = frozenset({BookingStatus.CANCELLED, BookingStatus.REFUSED})
"""Booking states whose chat takes nothing more: the appointment is not going ahead."""
LIMIT_EVENT = "chat.limit_reached"
"""The system event written once when a booking's chat reaches ``MAX_MESSAGES_PER_BOOKING``."""
WAITING_CAP = 20
"""The most waiting messages the page names when it reads; a longer list is cut (a page that
sent more than this in a minute is refused by the rate limit before)."""


class SendRefusal(StrEnum):
    """Why a participant's message was not sent, for a booking that has a chat."""

    EMPTY = "empty"
    TOO_LONG = "too_long"
    BAD_REQUEST = "bad_request"
    """The page's identifier of the message is not of the shape the page makes."""
    RATE_LIMITED = "rate_limited"
    """More messages in the last minute than ``embedded_call_chat_per_minute`` allows."""
    CLOSED = "closed"
    """The chat takes nothing more (the appointment is over, or the conversation ended)."""
    FULL = "full"
    """The booking's chat holds ``MAX_MESSAGES_PER_BOOKING`` messages."""
    UNAVAILABLE = "unavailable"
    """The contact center is not configured, refused or could not be reached; nothing was kept."""
    NO_CHAT = "no_chat"
    """The booking has no chat (not a conversation task on a service that uses the page)."""


@dataclass(frozen=True)
class ChatRead:
    """What the call page learns when it reads its chat.

    Attributes:
        messages: Messages that are new since the page's cursor, oldest first.
        states: The state of each message the page named as waiting, once it is not waiting any
            more (by message id).
        closed: Why the chat takes nothing more, or ``None`` while it is open.
    """

    messages: list[CallMessage] = field(default_factory=list)
    states: dict[int, ChatState] = field(default_factory=dict)
    closed: ChatClosed | None = None


class CallChatService:
    """Stores the deliveries of a conversation and reads a booking's thread.

    Args:
        uow: Opens database transactions.
        uses_call_page: Whether a service's participant gets the call page now
            (``app.domain.call_client.call_page_rule``); a booking without it has no chat.
        dispatchers: Gives the dispatcher choice for appending a participant's message (read at
            every send, because the integrations are wired again when settings change); without
            it nothing can be sent.
        clock: The time of a sent message and of the rate window; needed to send.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        uses_call_page: UsesCallPage,
        dispatchers: Callable[[], DispatcherChoice] | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._uses_page = uses_call_page
        self._dispatchers = dispatchers
        self._clock = clock

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

    def has_chat(self, booking: Booking, group: Group) -> bool:
        """Return whether ``booking`` has a chat now (a conversation task, a call-page service)."""
        task = booking.contact_center_task
        return has_chat(task.task_kind if task else None, self._uses_page(group))

    async def read(
        self, booking: Booking, group: Group, since: int, waiting: list[int]
    ) -> ChatRead | None:
        """Return what is new in the booking's chat, or ``None`` when it has none.

        Args:
            booking: The participant's booking, checked by the route.
            group: The booking's service.
            since: The id of the newest message the page already shows (0 for none); only newer
                ones are returned, so a read that brings nothing carries no message.
            waiting: The ids of the page's own messages that it still shows as waiting for the
                platform (cut at ``WAITING_CAP``); their state is returned once it changed.
        """
        if booking.id is None or not self.has_chat(booking, group):
            return None
        async with self._uow.read() as tx:
            thread = await tx.messages_of_call(booking.id)
            status = await tx.get_call_status(booking.id)
        asked = set(waiting[:WAITING_CAP])
        return ChatRead(
            [m for m in thread if (m.id or 0) > since],
            {
                m.id: m.state
                for m in thread
                if m.id in asked
                and m.author is ChatAuthor.PARTICIPANT
                and m.state is not ChatState.SENT
            },
            chat_closed(
                booking.status is BookingStatus.CONFIRMED,
                status.task if status else TaskState.NONE,
                thread,
            ),
        )

    async def send(
        self,
        booking: Booking,
        group: Group,
        text: str,
        client_id: str,
        *,
        max_chars: int,
        per_minute: int,
    ) -> CallMessage | SendRefusal:
        """Store a participant's message and append it to the contact-center task.

        The message is stored ``sent`` and committed before the platform is asked, so the
        platform's confirmation always finds it; when the platform cannot be reached the stored
        message is taken back and ``UNAVAILABLE`` is returned. The page's identifier is the
        message's key: the same one sent again (a retry after a failure, a double click) is the
        stored message, never a second one. The text is cleaned of control characters first.
        Nothing of the text reaches a log, an audit event or an error.

        Args:
            booking: The participant's booking, checked by the route.
            group: The booking's service.
            text: What the participant wrote.
            client_id: The page's identifier of the message (random letters and digits).
            max_chars: ``embedded_call_chat_max_chars`` of the organisation.
            per_minute: ``embedded_call_chat_per_minute``.

        Returns:
            The stored message, or why it was not sent.

        Raises:
            DomainError: ``invalid_configuration`` when the service was built without a
                dispatcher source or a clock (a wiring mistake, not a participant's).
        """
        if self._dispatchers is None or self._clock is None:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "chat_sending")
        task = booking.contact_center_task
        if booking.id is None or task is None or not self.has_chat(booking, group):
            return SendRefusal.NO_CHAT
        if not CLIENT_ID.fullmatch(client_id):
            return SendRefusal.BAD_REQUEST
        cleaned = clean_text(text)
        refusal = check_text(cleaned, max_chars)
        if refusal is not None:
            return SendRefusal.EMPTY if refusal is ChatRefusal.EMPTY else SendRefusal.TOO_LONG
        now = self._clock.now()
        provider_id = f"{PROVIDER_PREFIX}{client_id}"
        async with self._uow.locked() as tx:
            thread = await tx.messages_of_call(booking.id)
            for kept in thread:
                if kept.provider_message_id == provider_id:
                    return kept  # a retry of a message that is stored: never a second one
            status = await tx.get_call_status(booking.id)
            if chat_closed(
                booking.status is BookingStatus.CONFIRMED,
                status.task if status else TaskState.NONE,
                thread,
            ):
                return SendRefusal.CLOSED
            if len(thread) >= MAX_MESSAGES_PER_BOOKING:
                return SendRefusal.FULL
            mine = [m.at for m in thread if m.author is ChatAuthor.PARTICIPANT]
            if not within_rate(mine, now, per_minute):
                return SendRefusal.RATE_LIMITED
            message = CallMessage(
                booking.id, ChatAuthor.PARTICIPANT, cleaned, now, ChatState.SENT, provider_id
            )
            await tx.add_call_message(message)
            after = await tx.messages_of_call(booking.id)
            stored = next((m for m in after if m.provider_message_id == provider_id), message)
        if not await self._append(booking, group, provider_id, cleaned):
            async with self._uow.locked() as tx:
                await tx.withdraw_call_message(booking.id, provider_id)
            return SendRefusal.UNAVAILABLE
        return stored

    async def _append(self, booking: Booking, group: Group, provider_id: str, text: str) -> bool:
        """Hand the message to the contact center; ``False`` when it could not be reached."""
        task = booking.contact_center_task
        if task is None or self._dispatchers is None:
            return False
        try:
            dispatcher = await self._dispatchers().for_organisation(
                group.organisation_id, task.kind
            )
            if dispatcher is None:
                return False
            await dispatcher.append_message(task, ParticipantMessage(provider_id, text))
        except (IntegrationError, DomainError):
            return False
        return True


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
