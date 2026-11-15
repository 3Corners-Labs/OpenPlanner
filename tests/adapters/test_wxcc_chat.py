"""Reading the contact center's message events of a conversation (ADR 0123 step 7b)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.adapters.wxcc_chat import chat_key, parse_wxcc_chat
from app.domain.call_chat import (
    INBOUND_TEXT_CAP,
    ChatDirection,
    ChatEventKind,
    ChatSender,
)

AGENT: dict[str, Any] = json.loads(
    Path(__file__).with_name("wxcc_chat_agent_message.json").read_text()
)
FAILED: dict[str, Any] = json.loads(
    Path(__file__).with_name("wxcc_chat_append_failed.json").read_text()
)


def _message(**changes: Any) -> dict[str, Any]:
    """Return the recorded agent message with ``data`` fields replaced."""
    body = json.loads(json.dumps(AGENT))
    body["data"].update(changes)
    return body


def test_the_recorded_agent_message_is_read_as_a_delivered_outbound_message() -> None:
    event = parse_wxcc_chat(AGENT)

    assert event is not None
    assert (event.kind, event.task_id) == (ChatEventKind.APPENDED, "task-1")
    assert (event.direction, event.sender) == (ChatDirection.OUTBOUND, ChatSender.AGENT)
    assert event.message_id == "m-agent-1"
    assert event.message_text == "Goedemiddag, waarmee kan ik helpen?"
    assert event.at == datetime.fromtimestamp(1767600004, tz=UTC)


def test_nothing_of_the_sender_or_the_origin_is_kept() -> None:
    event = parse_wxcc_chat(AGENT)

    assert event is not None
    kept = repr(event) + str(vars(event))
    assert "agent-identifier-0001" not in kept  # the staff member's identifier at the platform
    assert "Zoë" not in kept  # the participant's first name in the origin
    assert "demo-address" not in kept


def test_a_refusal_after_the_end_names_the_task_and_nothing_else() -> None:
    event = parse_wxcc_chat(FAILED)

    assert event is not None
    assert (event.kind, event.task_id, event.message_id) == (
        ChatEventKind.APPEND_FAILED,
        "task-1",
        "",
    )


@pytest.mark.parametrize("event_type", ["task:new", "task:ended", "task-message:other", None])
def test_other_events_are_not_chat(event_type: str | None) -> None:
    body = {**AGENT, "type": event_type}

    assert parse_wxcc_chat(body) is None


def test_the_address_of_the_deliveries_reads_a_body_without_a_type_as_an_appended_message() -> None:
    bare = {**AGENT["data"]}  # no type and no ``data`` wrapper

    assert parse_wxcc_chat(bare) is None  # the subscription's address needs the type
    event = parse_wxcc_chat(bare, assume_appended=True)

    assert event is not None
    assert event.message_id == "m-agent-1"
    assert event.direction is ChatDirection.OUTBOUND


@pytest.mark.parametrize(
    "broken",
    [
        {"taskId": None},
        {"taskId": ""},
        {"channelParams": {"message": {"text": "  "}}},
        {"channelParams": {"message": {"aliasId": "m"}}},
        {"channelParams": "text"},
    ],
)
def test_an_event_without_a_task_or_without_text_is_ignored(broken: dict[str, Any]) -> None:
    assert parse_wxcc_chat(_message(**broken)) is None


def test_a_sender_that_is_not_an_agent_is_told_apart_from_one() -> None:
    system = parse_wxcc_chat(_message(senderType="system"))
    strange = parse_wxcc_chat(_message(senderType="bot"))

    assert system is not None
    assert strange is not None
    assert system.sender is ChatSender.SYSTEM
    assert strange.sender is ChatSender.OTHER


def test_an_inbound_message_is_the_participants_own_confirmed() -> None:
    event = parse_wxcc_chat(_message(messageDirection="INBOUND", senderType="customer"))

    assert event is not None
    assert event.direction is ChatDirection.INBOUND


def test_a_message_without_an_identifier_gets_a_digest_that_never_holds_the_text() -> None:
    body = _message(channelParams={"message": {"text": "tekst-zonder-id", "timestamp": 5}})

    first, second = parse_wxcc_chat(body), parse_wxcc_chat(body)

    assert first is not None
    assert first.message_id == (second.message_id if second else "")
    assert first.message_id.startswith("d:")
    assert "tekst-zonder-id" not in first.message_id


def test_a_long_text_is_cut_and_a_long_identifier_too() -> None:
    body = _message(
        channelParams={"message": {"aliasId": "x" * 500, "text": "y" * (INBOUND_TEXT_CAP + 10)}}
    )

    event = parse_wxcc_chat(body)

    assert event is not None
    assert len(event.message_text) == INBOUND_TEXT_CAP
    assert len(event.message_id) == 128


@pytest.mark.parametrize("stamp", [True, "nonsense", 10**30, None])
def test_a_time_that_cannot_be_read_is_none_and_the_gate_time_is_used_instead(stamp: Any) -> None:
    body = _message(
        channelParams={"message": {"aliasId": "m", "text": "hoi", "timestamp": stamp}},
        createdTime=None,
    )

    event = parse_wxcc_chat(body)

    assert event is not None
    assert event.at is None


def test_the_key_is_the_same_for_the_same_message_whichever_address_it_came_in_at() -> None:
    bare = {**AGENT["data"]}

    one, two = parse_wxcc_chat(AGENT), parse_wxcc_chat(bare, assume_appended=True)

    assert one is not None
    assert two is not None
    assert chat_key(one, AGENT) == chat_key(two, bare) != ""
    assert "Goedemiddag" not in chat_key(one, AGENT)


def test_a_refusal_is_keyed_by_the_event_id_and_without_one_by_nothing() -> None:
    refused = parse_wxcc_chat(FAILED)
    assert refused is not None

    assert chat_key(refused, FAILED) != ""
    assert chat_key(refused, {}) == ""
