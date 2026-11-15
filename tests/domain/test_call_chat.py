"""The chat's rules: when a booking has one, what may be sent, how fast (ADR 0123 step 7b)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.call_chat import (
    INBOUND_TEXT_CAP,
    CallMessage,
    ChatAuthor,
    ChatClosed,
    ChatEvent,
    ChatEventKind,
    ChatRefusal,
    ChatState,
    capped,
    chat_closed,
    check_text,
    clean_text,
    has_chat,
    within_rate,
)
from app.domain.calls import TaskState
from app.domain.contact_center import TaskKind

NOW = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("task_kind", "uses_page", "expected"),
    [
        (TaskKind.CONVERSATION, True, True),
        (
            TaskKind.CONVERSATION,
            False,
            False,
        ),  # the service did not choose the page (or the switch is off)
        (TaskKind.FORM, True, False),  # a work item has no chat, whatever the page is
        (None, True, False),  # no task, no chat
    ],
)
def test_a_booking_has_a_chat_only_for_a_conversation_on_a_service_with_the_page(
    task_kind: TaskKind | None, uses_page: bool, expected: bool
) -> None:
    assert has_chat(task_kind, uses_page) is expected


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        ("", ChatRefusal.EMPTY),
        ("   \n\t", ChatRefusal.EMPTY),
        ("a" * 201, ChatRefusal.TOO_LONG),
        ("a" * 200, None),
        ("Hallo, ik heb een vraag.", None),
    ],
)
def test_a_participant_message_is_not_empty_and_not_longer_than_the_setting(
    text: str, refusal: ChatRefusal | None
) -> None:
    assert check_text(text, 200) is refusal


def test_the_rate_counts_the_messages_of_the_last_minute_only() -> None:
    old = NOW - timedelta(minutes=1, seconds=1)
    just_in = NOW - timedelta(seconds=59)
    sent = [old, just_in, NOW]

    assert within_rate(sent, NOW, 3)  # two count, so a third is allowed
    assert not within_rate(sent, NOW, 2)
    assert within_rate([], NOW, 1)
    assert not within_rate([NOW] * 5, NOW, 5)


def test_a_delivered_text_is_cut_at_the_fixed_cap_which_is_the_top_of_the_setting() -> None:
    from app.domain.settings_registry import BY_KEY

    assert INBOUND_TEXT_CAP == BY_KEY["embedded_call_chat_max_chars"].maximum
    assert len(capped("x" * (INBOUND_TEXT_CAP + 50))) == INBOUND_TEXT_CAP


def test_the_text_never_reaches_a_repr_so_it_cannot_reach_a_log_line_by_accident() -> None:
    message = CallMessage(1, ChatAuthor.PARTICIPANT, "geheim-woord", NOW, ChatState.SENT, "m-1")
    event = ChatEvent(ChatEventKind.APPENDED, "task-1", message_text="geheim-woord")

    assert "geheim-woord" not in repr(message)
    assert "geheim-woord" not in repr(event)
    assert "geheim-woord" not in str(message)


@pytest.mark.parametrize(
    ("text", "cleaned"),
    [
        ("Hallo", "Hallo"),
        ("a\r\nb\rc", "a\nb\nc"),  # one kind of line end
        ("a\tb\nc", "a\tb\nc"),  # a tab and a line break stay
        ("a\x00b\x07c\x1bd\x7fe", "abcde"),  # the other control characters go
        ("tekst‮niet omgekeerd⁦", "tekstniet omgekeerd"),  # direction characters go
        ("  spaties blijven  ", "  spaties blijven  "),  # nothing is trimmed
        ("emoji 👍 en é", "emoji 👍 en é"),
    ],
)
def test_the_text_is_cleaned_of_control_and_direction_characters_only(
    text: str, cleaned: str
) -> None:
    assert clean_text(text) == cleaned


def _mine(state: ChatState) -> CallMessage:
    return CallMessage(1, ChatAuthor.PARTICIPANT, "x", NOW, state, "p1")


@pytest.mark.parametrize(
    ("open_", "task", "messages", "expected"),
    [
        (True, TaskState.QUEUED, [], None),
        (True, TaskState.CONNECTED, [_mine(ChatState.DELIVERED)], None),
        (True, TaskState.NONE, [_mine(ChatState.SENT)], None),
        (False, TaskState.CONNECTED, [], ChatClosed.ENDED),  # the appointment is over
        (False, TaskState.ENDED, [], ChatClosed.ENDED),  # over wins over a task that ended
        (True, TaskState.ENDED, [], ChatClosed.STAFF),
        (True, TaskState.FAILED, [], ChatClosed.STAFF),
        (True, TaskState.CANCELLED, [], ChatClosed.STAFF),
        (True, TaskState.CONNECTED, [_mine(ChatState.REFUSED)], ChatClosed.STAFF),
    ],
)
def test_the_chat_is_closed_when_the_appointment_is_over_or_the_conversation_ended(
    open_: bool, task: TaskState, messages: list[CallMessage], expected: ChatClosed | None
) -> None:
    assert chat_closed(open_, task, messages) is expected
