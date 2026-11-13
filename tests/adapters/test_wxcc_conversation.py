"""The conversation task at Webex Contact Center (ADR 0123 point 8, step 7a).

A ``customMessaging`` task replaces the work item for a routing that says so: same business
address, channel and flow settings, the booking reference as the participant's identifier and a
first text message. The request holds nothing of the booking that CLAUDE.md does not allow in an
outbound contact-center payload; a refusal of the platform is a domain error, never a work item.
"""

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.adapters.wxcc import (
    CONVERSATION_CHANNEL_TYPE,
    DESCRIPTOR,
    INTERACTION_KEY,
    ROUTING_KEY,
    SKILLS_KEY,
    WxccTasks,
    append_body,
    messaging_refusal,
    task_body,
)
from app.domain.calls import (
    CONVERSATION_OPEN,
    MESSAGING_NOT_CONFIGURED,
    ContactCenterTask,
    ParticipantMessage,
)
from app.domain.contact_center import (
    ContactCenterCapabilities,
    ContactCenterKind,
    ContactCenterTaskRef,
    TaskKind,
    uncarried,
)
from app.domain.interaction import InteractionKind
from app.domain.ports import IntegrationError
from tests.fakes.clock import FixedClock
from tests.fakes.tokens import authorized_client
from tests.support.domain import NOW, webex_routing

RECORDED = json.loads(Path(__file__).with_name("wxcc_conversation.json").read_text())
STAMP = 1767600000000
TASK_ID = "11111111-2222-4333-8444-555555555555"
OPENING = (
    "Videoafspraak OP-000123 met Zoë, 5 jan 2026, 09:00, Demo. "
    "Start het gesprek: https://site.webex.com/m/1"
)
CONVERSATION = ContactCenterTask(
    reference="OP-000123",
    first_name="Zoë",
    start_label="5 jan 2026, 09:00",
    group_name="Demo",
    meeting_link="https://site.webex.com/m/1",
    kind="scheduled",
    routing=webex_routing("openplanner@example.biz", "openplanner-chat"),
    routing_value="demo",
    task_kind=TaskKind.CONVERSATION,
    first_message=OPENING,
)
REF = ContactCenterTaskRef(TASK_ID, ContactCenterKind.WEBEX_CC, TaskKind.CONVERSATION)


def _wire(body: dict[str, Any]) -> str:
    """Return ``body`` as ``httpx`` puts a ``json=`` body on the wire."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _tasks(handler: Callable[[httpx.Request], httpx.Response]) -> WxccTasks:
    return WxccTasks(authorized_client(handler), "https://api.wxcc-eu1.cisco.com", FixedClock(NOW))


def test_the_descriptor_declares_the_conversation() -> None:
    assert DESCRIPTOR.capabilities.carries_task(TaskKind.CONVERSATION)
    assert CONVERSATION_CHANNEL_TYPE == "customMessaging"


def test_the_create_request_is_the_recorded_one() -> None:
    body = task_body(CONVERSATION, "alias-1", STAMP)

    assert _wire(body) == RECORDED["create"]["request"]["body"]


def test_the_create_request_holds_exactly_these_keys() -> None:
    body = task_body(CONVERSATION, "alias-1", STAMP)

    assert tuple(body) == (
        "origin",
        "destination",
        "channelType",
        "channel",
        "flowSettings",
        "channelParams",
    )
    assert body["origin"] == {"id": "OP-000123", "name": "Zoë"}
    assert body["destination"] == {"id": "openplanner@example.biz", "type": "businessAddress"}
    assert body["channelType"] == "customMessaging"
    assert tuple(body["channelParams"]) == ("type", "message")
    assert body["channelParams"]["type"] == "text"
    assert tuple(body["channelParams"]["message"]) == ("aliasId", "text", "timestamp")
    assert body["channelParams"]["message"]["text"] == OPENING


def test_a_conversation_carries_the_same_flow_settings_as_a_work_item() -> None:
    varied = replace(
        CONVERSATION,
        variables=(("lang", "nl"), ("topic", "MOVE")),
        skills=("intake_1", "lang_de"),
        interaction=InteractionKind.NONE,
        meeting_link="",
    )
    work_item = replace(varied, task_kind=TaskKind.FORM, first_message="")

    assert (
        task_body(varied, "a", STAMP)["flowSettings"]
        == task_body(work_item, "a", STAMP)["flowSettings"]
        == {
            "lang": "nl",
            "topic": "MOVE",
            SKILLS_KEY: "intake_1,lang_de",
            INTERACTION_KEY: "none",
            ROUTING_KEY: "demo",
        }
    )


def test_no_other_field_of_the_booking_reaches_the_request() -> None:
    """The request holds the task's allowed fields; anything else would show up as a value."""
    wire = _wire(task_body(CONVERSATION, "alias-1", STAMP))

    for allowed in ("OP-000123", "Zoë", "5 jan 2026, 09:00", "Demo", "https://site.webex.com/m/1"):
        assert allowed in wire
    # The task type cannot hold a surname, an address, a number or a form answer, so none can
    # be sent; the request is exactly the keys above and the strings of the allowed fields.
    allowed_values = {
        CONVERSATION.reference,
        CONVERSATION.first_name,
        CONVERSATION.start_label,
        CONVERSATION.group_name,
        CONVERSATION.meeting_link,
        CONVERSATION.routing_value,
        "openplanner@example.biz",
        "openplanner-chat",
        "alias-1",
        OPENING,
    }
    assert _strings(task_body(CONVERSATION, "alias-1", STAMP)) - allowed_values <= {
        "businessAddress",
        "customMessaging",
        "text",
        "origin",
        "destination",
        "channelType",
        "channel",
        "flowSettings",
        "channelParams",
        "message",
        "aliasId",
        "timestamp",
        "id",
        "name",
        "type",
        ROUTING_KEY,
    }


def _strings(value: object) -> set[str]:
    """Return every key and string value in ``value``, whole."""
    if isinstance(value, dict):
        return {str(k) for k in value} | {s for v in value.values() for s in _strings(v)}
    return {value} if isinstance(value, str) else set()


@pytest.mark.parametrize("text", ["", "   ", "x" * 20_001])
def test_a_conversation_without_a_usable_first_message_is_refused_before_it_is_sent(
    text: str,
) -> None:
    with pytest.raises(IntegrationError, match="not_ready"):
        task_body(replace(CONVERSATION, first_message=text), "alias-1", STAMP)


def test_a_platform_that_does_not_declare_the_conversation_is_never_asked_for_one() -> None:
    without = ContactCenterCapabilities(
        frozenset({InteractionKind.VIDEO}), skills=True, task_kinds=frozenset({TaskKind.FORM})
    )

    assert uncarried(without, InteractionKind.VIDEO, False, TaskKind.CONVERSATION) == "task_kind"


async def test_the_task_is_created_and_the_recorded_response_read() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        recorded = RECORDED["create"]["response"]
        return httpx.Response(recorded["status"], json=recorded["body"])

    task_id = await _tasks(handler).create_task(CONVERSATION)

    assert task_id == TASK_ID
    assert (seen[0].method, seen[0].url.path) == ("POST", RECORDED["create"]["request"]["path"])
    sent = json.loads(seen[0].content)
    assert sent["channelType"] == "customMessaging"
    assert sent["channelParams"]["message"]["timestamp"] == int(NOW.timestamp() * 1000)


def test_the_append_request_is_the_recorded_one() -> None:
    body = append_body(ParticipantMessage("msg-0001", "Ik ben er over vijf minuten."), STAMP)

    assert _wire(body) == RECORDED["append"]["request"]["body"]


async def test_a_message_is_appended_to_the_conversation() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        recorded = RECORDED["append"]["response"]
        return httpx.Response(recorded["status"], json=recorded["body"])

    await _tasks(handler).append_message(REF, ParticipantMessage("msg-0001", "Hallo"))

    assert (seen[0].method, seen[0].url.path) == ("POST", RECORDED["append"]["request"]["path"])
    sent = json.loads(seen[0].content)
    assert sent["mediaType"] == "customMessaging"
    assert sent["channelParams"]["message"]["aliasId"] == "msg-0001"


async def test_the_same_message_identifier_twice_is_the_same_alias_twice() -> None:
    """The platform takes one aliasId as one message, so a retry cannot double a message."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(202, json={"data": {"id": TASK_ID}})

    tasks = _tasks(handler)
    message = ParticipantMessage("msg-0001", "Hallo")
    await tasks.append_message(REF, message)
    await tasks.append_message(REF, message)

    aliases = [body["channelParams"]["message"]["aliasId"] for body in sent]
    assert aliases == ["msg-0001", "msg-0001"]


@pytest.mark.parametrize(
    "task",
    [
        ContactCenterTaskRef(TASK_ID, ContactCenterKind.WEBEX_CC, TaskKind.FORM),
        ContactCenterTaskRef(TASK_ID, ContactCenterKind.WEBEX_CC),
    ],
)
async def test_nothing_is_appended_to_a_work_item(task: ContactCenterTaskRef) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    with pytest.raises(IntegrationError) as error:
        await _tasks(handler).append_message(task, ParticipantMessage("m1", "Hallo"))

    assert (error.value.code, error.value.retryable) == ("not_ready", False)
    assert seen == []


async def test_a_403_is_messaging_not_configured_and_never_a_work_item() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        recorded = RECORDED["refused_without_messaging"]["response"]
        return httpx.Response(recorded["status"], json=recorded["body"])

    with pytest.raises(IntegrationError) as error:
        await _tasks(handler).create_task(CONVERSATION)

    assert (error.value.code, error.value.retryable) == (MESSAGING_NOT_CONFIGURED, False)
    assert error.value.reason == "HTTP 403"  # a 403 has more than one cause: the status stays
    assert [r.url.path for r in seen] == ["/v2/tasks"]  # one request: no second, as a work item
    assert json.loads(seen[0].content)["channelType"] == "customMessaging"


async def test_a_second_open_conversation_is_conversation_open() -> None:
    # The status is the one the platform's refusal of a second conversation is expected to
    # carry; it was not recorded on the lab tenant (ADR 0123 step 7a, report).
    tasks = _tasks(lambda request: httpx.Response(409, json={"error": {"key": "409"}}))

    with pytest.raises(IntegrationError) as error:
        await tasks.create_task(CONVERSATION)

    assert (error.value.code, error.value.retryable) == (CONVERSATION_OPEN, False)
    assert error.value.reason == "HTTP 409"


async def test_other_refusals_of_a_conversation_keep_their_own_codes() -> None:
    wrong_channel = _tasks(lambda request: httpx.Response(400, json={"error": "Invalid channel"}))
    down = _tasks(lambda request: httpx.Response(503))

    with pytest.raises(IntegrationError) as refused:
        await wrong_channel.create_task(CONVERSATION)
    with pytest.raises(IntegrationError) as unavailable:
        await down.create_task(CONVERSATION)

    assert (refused.value.code, refused.value.retryable) == ("rejected_400", False)
    assert unavailable.value.retryable is True


async def test_a_403_on_a_work_item_is_still_the_plain_refusal() -> None:
    tasks = _tasks(lambda request: httpx.Response(403, json={"error": {"key": "403"}}))
    item = replace(CONVERSATION, task_kind=TaskKind.FORM, first_message="")

    with pytest.raises(IntegrationError) as error:
        await tasks.create_task(item)

    assert error.value.code == "rejected_403"


async def test_an_append_refused_for_missing_messaging_is_the_same_domain_error() -> None:
    tasks = _tasks(lambda request: httpx.Response(403))

    with pytest.raises(IntegrationError) as error:
        await tasks.append_message(REF, ParticipantMessage("m1", "Hallo"))

    assert error.value.code == MESSAGING_NOT_CONFIGURED


@pytest.mark.parametrize("status", [403, 409])
async def test_only_the_status_reaches_the_error_never_the_platforms_description(
    status: int,
) -> None:
    """The request holds the first name and the link; a description of the error may echo them."""
    body = {"error": {"description": "Zoë https://site.webex.com/m/1 refused", "key": "x"}}
    tasks = _tasks(lambda request: httpx.Response(status, json=body))

    with pytest.raises(IntegrationError) as created:
        await tasks.create_task(CONVERSATION)
    with pytest.raises(IntegrationError) as appended:
        await tasks.append_message(REF, ParticipantMessage("m1", "Hallo"))

    for error in (created.value, appended.value):
        assert error.reason == f"HTTP {status}"
        for echoed in ("Zoë", "site.webex.com", "refused"):
            assert echoed not in f"{error.reason} {error!s} {error!r} {error.args}"


def test_a_reason_of_the_platform_is_dropped_too() -> None:
    """Even a reason an adapter had put on the error (a request without personal data)."""
    refused = IntegrationError("rejected_403", retryable=False, reason="Zoë at https://x.nl")

    assert messaging_refusal(refused).reason == "HTTP 403"
