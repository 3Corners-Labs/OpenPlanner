"""Reading Webex Contact Center's message events of a conversation task (ADR 0123 step 7b).

Two kinds of delivery carry the chat, and both arrive verified by the gate of ADR 0124 before this
module sees them:

- the **event subscription** (``/webhooks/contact-center/webex_cc/{org}``):
  ``task-message:appended`` confirms a message we appended (inbound) and
  ``task-message:append-failed`` says a message that was accepted was refused afterwards (the
  task has ended); the subscription may also report the outbound ones;
- the **asset's delivery address** (``/webhooks/contact-center/webex_cc/{org}/messages``): every
  message the flow or the staff member sends, as ``task-message:appended`` with
  ``messageDirection: OUTBOUND`` and ``senderType`` ``agent`` or ``system``.

The shape was measured on the lab tenant (``docs/research/call-page-on-the-meetings-sdk.md``,
9.3): ``taskId``, ``messageDirection``, ``senderType``, ``senderId``, ``channelParams.message``
(``aliasId``, ``text``, ``timestamp``) and ``createdTime``, under ``data`` like the task events.
Assumption of this step: a delivery to the asset's address may come without the ``type`` and
without the ``data`` wrapper; the address says what it carries, so there the type defaults to
``task-message:appended`` and the fields are read from the body when there is no ``data``.

Only what the chat needs is read: the task id, the direction, the kind of sender, the message's
identifier, its text and its time. The staff member's identifier (``senderId``), the participant's
origin and every attachment are never read, so they are never kept.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any

from app.domain.call_chat import (
    ChatDirection,
    ChatEvent,
    ChatEventKind,
    ChatSender,
    capped,
)

CHAT_EVENTS: dict[str, ChatEventKind] = {
    "task-message:appended": ChatEventKind.APPENDED,
    "task-message:append-failed": ChatEventKind.APPEND_FAILED,
}
"""The event types of a conversation's messages that OpenPlanner acts on."""

ID_LIMIT = 128
"""The longest message identifier kept (the column's length); a longer one is cut."""


def parse_wxcc_chat(payload: dict[str, Any], *, assume_appended: bool = False) -> ChatEvent | None:
    """Map a message event of a conversation task; ``None`` if it is not one OpenPlanner reads.

    Args:
        payload: The verified JSON object.
        assume_appended: The body came to the address of the asset's deliveries, which carries
            nothing but appended messages: a body without a type is one.

    Returns:
        The event, or ``None`` for another event type, a body without a task id, or an appended
        message without text.
    """
    event_type = payload.get("type")
    if event_type is None and assume_appended:
        kind: ChatEventKind | None = ChatEventKind.APPENDED
    else:
        kind = CHAT_EVENTS.get(str(event_type))
    if kind is None:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        data = payload
    task_id = data.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        return None
    if kind is ChatEventKind.APPEND_FAILED:
        return ChatEvent(kind, task_id)
    message = _message_of(data)
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    direction = (
        ChatDirection.OUTBOUND
        if str(data.get("messageDirection", "")).upper() == "OUTBOUND"
        else ChatDirection.INBOUND
    )
    return ChatEvent(
        kind,
        task_id,
        direction,
        _sender_of(data.get("senderType")),
        _message_id(message, task_id, direction, text),
        capped(text),
        _time_of(message.get("timestamp"), data.get("createdTime")),
    )


def _message_of(data: dict[str, Any]) -> dict[str, Any]:
    """Return ``channelParams.message`` of an event, or an empty object."""
    params = data.get("channelParams")
    message = params.get("message") if isinstance(params, dict) else None
    return message if isinstance(message, dict) else {}


def _sender_of(value: object) -> ChatSender:
    """Return who sent an outbound message; anything but ``agent`` or ``system`` is ``other``."""
    try:
        return ChatSender(str(value).lower())
    except ValueError:
        return ChatSender.OTHER


def _message_id(message: dict[str, Any], task_id: str, direction: ChatDirection, text: str) -> str:
    """Return the message's identifier: its ``aliasId``, else a digest that never holds the text.

    A message without an identifier of its own is told apart by the task, the direction, its time
    and a digest of the text, so a redelivery is still one message; the digest is one-way and
    the text is not stored in it.
    """
    alias = message.get("aliasId") or message.get("id")
    if isinstance(alias, str) and alias:
        return alias[:ID_LIMIT]
    parts = (task_id, direction.value, str(message.get("timestamp", "")), text)
    return "d:" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _time_of(*candidates: object) -> datetime | None:
    """Return the first usable time: epoch milliseconds, or an ISO text; else ``None``."""
    for value in candidates:
        try:
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                return datetime.fromtimestamp(value / 1000, tz=UTC)
            if isinstance(value, str) and value:
                found = datetime.fromisoformat(value)
                return found if found.tzinfo else found.replace(tzinfo=UTC)
        except (OverflowError, OSError, ValueError):
            continue
    return None


def chat_key(event: ChatEvent, payload: dict[str, Any]) -> str:
    """Return the digest that identifies one chat event for the replay memory, or ``""``.

    A message is identified by its task, direction and identifier, so the same message reaching
    both addresses is one event. A refusal carries only the task, so it is identified by the
    event's own ``id`` when it has one. Only ids and types go in, never the text.
    """
    identity = event.message_id or str(payload.get("id") or "")
    if not identity:
        return ""
    parts = ("wxcc-chat", event.kind.value, event.task_id, event.direction.value, identity)
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
