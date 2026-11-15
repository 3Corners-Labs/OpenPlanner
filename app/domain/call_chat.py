"""The chat beside the participant's call: its messages and its rules (ADR 0123 point 8, step 7b).

A booking of a service that uses the call page, whose contact-center task was made as a
conversation (``TaskKind.CONVERSATION``), has a chat: the participant writes in the side bar, the
staff member answers in their agent desktop, and the platform carries both through the
conversation task. Messages are kept with the booking so a reload shows the thread and a late
reply is not lost (``call_messages``). Pure rules, no framework:

    has_chat(task kind, service uses the page) ─► whether a booking has a chat at all
    check_text(text, max_chars)                ─► a participant's message may be sent
    within_rate(sent_at, now, per_minute)      ─► the participant is not flooding the desktop
    ChatEvent                                  ─► what a verified delivery says, platform-neutral

The text of a message is personal data (free text of a participant, or a staff member's words to
them): it lives only in ``CallMessage.text``, is on the one list of ``app.domain.privacy``
(``CHAT_FIELDS``, the field is called ``message_text`` everywhere so the log mask finds it), is
deleted by the booking's purge and is never in a log, an audit event, a job payload or an address.
``CallMessage`` and ``ChatEvent`` keep it out of their ``repr``.

The author is a kind, never a name: a staff member's message carries an identifier at the contact
center and no name, and the name the participant sees is worked out when the thread is read, from
the organisation's ``embedded_call_staff_name`` and our own record of who accepted
(``app.domain.screen_name``), so nothing about who wrote is stored here.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.contact_center import TaskKind

INBOUND_TEXT_CAP = 4000
"""The most characters of a staff member's message that are kept (the top of the setting
``embedded_call_chat_max_chars``): a fixed rule, not a setting. What the participant may send is
that setting; what the platform delivers is cut here, so one delivery cannot fill the table."""

RATE_WINDOW = timedelta(minutes=1)
"""The window ``embedded_call_chat_per_minute`` counts messages in."""


class ChatAuthor(StrEnum):
    """Who wrote a message: the kind only, never a name."""

    PARTICIPANT = "participant"
    STAFF = "staff"


class ChatState(StrEnum):
    """Where a message stands at the platform (ADR 0123 point 8, *Accepted is not delivered*)."""

    SENT = "sent"
    """Appended to the task and accepted (202); the platform has not confirmed it yet."""
    DELIVERED = "delivered"
    """Confirmed by ``task-message:appended``; a staff member's message is stored as this."""
    REFUSED = "refused"
    """Refused afterwards by ``task-message:append-failed``: the conversation is closed."""


class ChatRefusal(StrEnum):
    """Why a participant's message is not sent; a code, never user-facing text."""

    EMPTY = "empty"
    TOO_LONG = "too_long"


class ChatEventKind(StrEnum):
    """The two events of the platform that concern a chat."""

    APPENDED = "appended"
    """``task-message:appended``: a message is in the conversation (either direction)."""
    APPEND_FAILED = "append_failed"
    """``task-message:append-failed``: a message that was accepted was refused afterwards."""


class ChatDirection(StrEnum):
    """Which way a delivered message went, as the platform says it."""

    INBOUND = "inbound"
    """From the participant (the messages we appended)."""
    OUTBOUND = "outbound"
    """To the participant: from the flow or a staff member."""


class ChatSender(StrEnum):
    """Who sent an outbound message, as the platform says it."""

    AGENT = "agent"
    SYSTEM = "system"
    OTHER = "other"


@dataclass(frozen=True)
class ChatEvent:
    """What a verified delivery of the platform says about a conversation, platform-neutral.

    Attributes:
        kind: Appended or refused.
        task_id: The conversation task it belongs to; the only way a delivery finds a booking.
        direction: Which way the message went (``INBOUND`` when the platform does not say).
        sender: Who sent an outbound message.
        message_id: The platform's identifier of the message; ``""`` for ``APPEND_FAILED``,
            which carries only the task's.
        message_text: The message text, cut at ``INBOUND_TEXT_CAP``; personal data, kept out of
            ``repr``.
        at: When the platform made the message; ``None`` when it did not say (the time the gate
            used is taken then).
    """

    kind: ChatEventKind
    task_id: str
    direction: ChatDirection = ChatDirection.INBOUND
    sender: ChatSender = ChatSender.OTHER
    message_id: str = ""
    message_text: str = field(default="", repr=False)
    at: datetime | None = None


@dataclass(frozen=True)
class CallMessage:
    """One message of a booking's chat, as stored.

    Attributes:
        booking_id: The booking it belongs to.
        author: Participant or staff.
        message_text: The text; personal data, kept out of ``repr``.
        at: When it was written (UTC).
        state: Where it stands at the platform.
        provider_message_id: The platform's identifier of the message, the idempotence key of a
            delivery (unique per booking); ours for a message the participant sent.
        id: The row's id once stored.
    """

    booking_id: int
    author: ChatAuthor
    message_text: str = field(repr=False)
    at: datetime
    state: ChatState
    provider_message_id: str
    id: int | None = None


def has_chat(task_kind: TaskKind | None, uses_call_page: bool) -> bool:
    """Return whether a booking has a chat: its task is a conversation on a service with the page.

    Args:
        task_kind: The kind the booking's task was made as; ``None`` for no task.
        uses_call_page: The answer of ``app.domain.call_client.uses_call_page`` for the service
            (false while the platform switch ``embedded_call_enabled`` is off).
    """
    return uses_call_page and task_kind is TaskKind.CONVERSATION


def check_text(text: str, max_chars: int) -> ChatRefusal | None:
    """Return why a participant's message may not be sent, or ``None`` when it may.

    A message of only white space is empty. Length counts characters of the text as written
    (``embedded_call_chat_max_chars``).
    """
    if not text.strip():
        return ChatRefusal.EMPTY
    if len(text) > max_chars:
        return ChatRefusal.TOO_LONG
    return None


def within_rate(sent: list[datetime], now: datetime, per_minute: int) -> bool:
    """Return whether the participant may send another message now.

    Args:
        sent: The times the booking's participant messages were sent, any order.
        now: The time of the attempt.
        per_minute: ``embedded_call_chat_per_minute``.
    """
    recent = sum(1 for at in sent if now - RATE_WINDOW < at <= now)
    return recent < per_minute


def capped(text: str) -> str:
    """Return a delivered text cut at ``INBOUND_TEXT_CAP`` characters."""
    return text[:INBOUND_TEXT_CAP]
