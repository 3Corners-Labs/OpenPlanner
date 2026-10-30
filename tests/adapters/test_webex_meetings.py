"""Webex Meetings adapter against recorded responses (contract: docs/webhooks.md)."""

import json
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
import pytest
from structlog.testing import capture_logs

from app.adapters.webex_meetings import WebexMeetings
from app.domain.booking import MeetingRef
from app.domain.calls import MeetingRequest
from app.domain.ports import IntegrationError
from tests.fakes.clock import FixedClock
from tests.fakes.tokens import authorized_client
from tests.support.domain import NOW, SLOT_0900

REQUEST = MeetingRequest(
    "Videogesprek Demo",
    SLOT_0900,
    SLOT_0900 + timedelta(minutes=30),
    "Europe/Amsterdam",
    "host@example.nl",
)
CREATED = {  # recorded shape of POST /v1/meetings (trimmed)
    "id": "870f51ff287b41be84648412901e0402",
    "meetingNumber": "25869964567",
    "title": "Videogesprek Demo",
    "webLink": "https://site.webex.com/site/j.php?MTID=m12345",
    "hostEmail": "host@example.nl",
}


def _meetings(
    handler: Callable[[httpx.Request], httpx.Response], now: datetime = NOW
) -> WebexMeetings:
    return WebexMeetings(authorized_client(handler), "https://webexapis.com/v1", FixedClock(now))


async def test_a_direct_call_is_sent_in_whole_seconds_from_now() -> None:
    """Webex refuses microseconds and a start over a minute ago (ADR 0070)."""
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=CREATED)

    started = SLOT_0900 + timedelta(seconds=3, microseconds=473110)  # a direct call: "now"
    direct = MeetingRequest(
        "Videogesprek Demo", started, started + timedelta(minutes=30), "Europe/Amsterdam", "h@x.nl"
    )
    retry = started + timedelta(minutes=1, seconds=2)  # the worker's first retry (ADR 0019)

    await _meetings(handler, now=started).create_meeting(direct)
    await _meetings(handler, now=retry).create_meeting(direct)

    assert (seen[0]["start"], seen[0]["end"]) == (
        "2026-01-05T09:00:03+01:00",
        "2026-01-05T09:30:03+01:00",
    )
    assert seen[1]["start"] == "2026-01-05T09:01:05+01:00"  # moved to now, end unchanged
    assert seen[1]["end"] == "2026-01-05T09:30:03+01:00"


async def test_meeting_is_created_with_the_agreed_defaults() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=CREATED)

    meeting = await _meetings(handler).create_meeting(REQUEST)

    assert meeting == MeetingRef("870f51ff287b41be84648412901e0402", CREATED["webLink"])
    body = seen[0]
    assert body["hostEmail"] == "host@example.nl"
    assert body["allowAnyUserToBeCoHost"] is True
    assert body["enabledJoinBeforeHost"] is False
    assert body["unlockedMeetingJoinSecurity"] == "allowJoinWithLobby"
    assert body["sendEmail"] is False
    assert body["meetingOptions"] == {
        "enabledChat": True,
        "enabledVideo": True,
        "enabledFileTransfer": False,
    }
    # In the group's time zone: Webex refuses a zone that does not match the offset (ADR 0065).
    assert (body["start"], body["end"]) == (
        "2026-01-05T09:00:00+01:00",
        "2026-01-05T09:30:00+01:00",
    )
    assert body["timezone"] == "Europe/Amsterdam"


async def test_deleting_a_meeting_that_is_gone_is_fine() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    await _meetings(handler).delete_meeting("m-1", "host@example.nl")

    assert calls[0].method == "DELETE"
    assert calls[0].url.path == "/v1/meetings/m-1"
    assert calls[0].url.params["hostEmail"] == "host@example.nl"


async def test_unexpected_answer_is_not_retried() -> None:
    meetings = _meetings(lambda request: httpx.Response(200, json={"id": "x"}))

    with pytest.raises(IntegrationError, match="unexpected_response"):
        await meetings.create_meeting(REQUEST)


async def test_a_refused_meeting_logs_and_carries_webexs_reason() -> None:
    body = {"message": "Invalid hostEmail.", "trackingId": "ROUTER_789"}
    meetings = _meetings(lambda request: httpx.Response(400, json=body))

    with capture_logs() as logs, pytest.raises(IntegrationError, match="rejected_400") as error:
        await meetings.create_meeting(REQUEST)

    refused = next(entry for entry in logs if entry["event"] == "integration.refused")
    assert (refused["reason"], refused["tracking_id"]) == ("Invalid hostEmail.", "ROUTER_789")
    assert error.value.reason == "Invalid hostEmail."  # ADR 0065: shown on the timeline
