"""WxCC work item adapter: the request body is the contract verified in ADR 0018."""

import json
from collections.abc import Callable

import httpx
import pytest

from app.adapters.wxcc import WORK_ITEM_FIELDS, WxccTasks, cancellation_body, task_body
from app.domain.calls import WorkItem
from app.domain.ports import IntegrationError
from tests.fakes.clock import FixedClock
from tests.fakes.tokens import authorized_client
from tests.support.domain import NOW

ITEM = WorkItem(
    reference="OP-000123",
    first_name="Ada",
    start_label="5 jan 2026, 09:00",
    group_name="Demo: videogesprek",
    meeting_link="https://site.webex.com/m/1",
    kind="scheduled",
    business_address="openplanner@example.biz",
    channel="openplanner-video",
    routing_value="demo",
)


def test_task_body_matches_the_create_task_v2_contract() -> None:
    body = task_body(ITEM, "alias-1", 1767600000000)

    assert body == {
        "origin": {"id": "OP-000123", "name": "Ada"},
        "destination": {"id": "openplanner@example.biz", "type": "businessAddress"},
        "channelType": "workItem",
        "channel": "openplanner-video",
        "flowSettings": {"openplannerGroup": "demo"},
        "channelParams": {
            "type": "work-item-form",
            "message": {
                "aliasId": "alias-1",
                "workItemData": {
                    "bookingReference": "OP-000123",
                    "firstName": "Ada",
                    "startTime": "5 jan 2026, 09:00",
                    "groupName": "Demo: videogesprek",
                    "meetingLink": "https://site.webex.com/m/1",
                    "kind": "scheduled",
                },
                "timestamp": 1767600000000,
            },
        },
    }
    # Limits from the spec: at most 50 fields, values up to 1024 bytes.
    data = body["channelParams"]["message"]["workItemData"]  # type: ignore[index]
    assert tuple(data) == WORK_ITEM_FIELDS  # the schema check in wxcc-config-check uses these
    assert len(data) <= 50
    assert all(len(str(v).encode()) <= 1024 for v in data.values())


def _tasks(handler: Callable[[httpx.Request], httpx.Response]) -> WxccTasks:
    return WxccTasks(authorized_client(handler), "https://api.wxcc-eu1.cisco.com", FixedClock(NOW))


async def test_task_is_created_and_its_id_returned() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={
                "meta": {"orgId": "cb5901fb-ab66-4377-a7bc-5f0b21896952"},
                "data": {"id": "141897a0-6212-49be-be9d-4823ab1e4dc7"},
            },
        )

    task_id = await _tasks(handler).create_task(ITEM)

    assert task_id == "141897a0-6212-49be-be9d-4823ab1e4dc7"
    assert seen[0].url.path == "/v2/tasks"
    body = json.loads(seen[0].content)
    assert body["channelParams"]["message"]["timestamp"] == int(NOW.timestamp() * 1000)


async def test_schema_violation_is_not_retried() -> None:
    tasks = _tasks(lambda request: httpx.Response(400, json={"error": "INVALID_CONTENT"}))

    with pytest.raises(IntegrationError) as error:
        await tasks.create_task(ITEM)
    assert (error.value.code, error.value.retryable) == ("rejected_400", False)


async def test_end_task_calls_the_end_task_api() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"data": {"id": "141897a0"}})

    await _tasks(handler).end_task("141897a0-6212-49be-be9d-4823ab1e4dc7")

    assert (seen[0].method, seen[0].url.path) == (
        "POST",
        "/v1/tasks/141897a0-6212-49be-be9d-4823ab1e4dc7/end",
    )


async def test_end_task_without_the_agent_scope_is_not_retried() -> None:
    # Recorded: a Service App token without cjp:user gets 403 from End Task.
    tasks = _tasks(lambda request: httpx.Response(403, json={"error": {"key": "403"}}))

    with pytest.raises(IntegrationError) as error:
        await tasks.end_task("141897a0-6212-49be-be9d-4823ab1e4dc7")
    assert (error.value.code, error.value.retryable) == ("rejected_403", False)


async def test_a_task_id_cannot_change_the_path() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    await _tasks(handler).end_task("../v2/subscriptions")

    assert seen[0].url.raw_path == b"/v1/tasks/..%2Fv2%2Fsubscriptions/end"


def test_cancellation_message_carries_the_reference_only() -> None:
    body = cancellation_body("OP-000123", "alias-2", 1767600000000)

    assert body == {
        "mediaType": "workItem",
        "channelParams": {
            "type": "work-item-form",
            "message": {
                "aliasId": "alias-2",
                "workItemData": {"bookingReference": "OP-000123", "kind": "cancelled"},
                "timestamp": 1767600000000,
            },
        },
    }


async def test_mark_cancelled_appends_a_task_message() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"data": {"id": "event-1"}})

    await _tasks(handler).mark_cancelled("141897a0", "OP-000123")

    assert seen[0].url.path == "/v2/tasks/141897a0/messages"
    message = json.loads(seen[0].content)["channelParams"]["message"]
    assert message["workItemData"] == {"bookingReference": "OP-000123", "kind": "cancelled"}
