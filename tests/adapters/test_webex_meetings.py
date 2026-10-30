"""Webex Meetings adapter against recorded responses (contract: docs/webhooks.md)."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from structlog.testing import capture_logs

from app.adapters.webex_meetings import WebexMeetings
from app.domain.booking import MeetingRef
from app.domain.calls import CallState, MeetingRequest, MeetingStatus
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
    """Webex refuses microseconds and a start over a minute ago (ADR 0072)."""
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


async def test_a_trial_meeting_differs_only_in_who_may_enter_first() -> None:
    from dataclasses import replace

    from app.adapters.webex_meetings import meeting_body

    booking = meeting_body(REQUEST, NOW)
    trial = meeting_body(replace(REQUEST, join_before_host=True), NOW)

    assert trial["enabledJoinBeforeHost"] is True
    assert trial["enableConnectAudioBeforeHost"] is True
    assert trial["unlockedMeetingJoinSecurity"] == "allowJoin"
    same = {k: v for k, v in trial.items() if k in booking and trial[k] == booking[k]}
    assert same["hostEmail"] == "host@example.nl"
    assert same["meetingOptions"] == booking["meetingOptions"]
    assert same["sendEmail"] is False
    assert same["allowAnyUserToBeCoHost"] is True
    assert "enableConnectAudioBeforeHost" not in booking


# Shapes of GET /v1/meetings/{id} and GET /v1/meetingParticipants (trimmed to the fields the
# adapter reads, plus the personal ones it must ignore), written from the Webex reference.
MEETING_IN_PROGRESS = {"id": "m-1", "state": "inProgress", "hostEmail": "host@example.nl"}
PARTICIPANTS = {
    "items": [
        {  # the agent: cohost, in the call
            "id": "p-1",
            "host": False,
            "coHost": True,
            "displayName": "Mo Medewerker",
            "email": "mo@example.nl",
            "state": "joined",
            "joinedTime": "2026-01-05T08:00:40Z",
        },
        {  # the participant, in the call
            "id": "p-2",
            "host": False,
            "coHost": False,
            "displayName": "Ada Lovelace",
            "email": "ada@example.org",
            "state": "joined",
            "joinedTime": "2026-01-05T08:01:10Z",
        },
        {  # a guest still in the lobby
            "id": "p-3",
            "displayName": "Grace Hopper",
            "email": "grace@example.org",
            "state": "lobby",
        },
        {  # someone who left
            "id": "p-4",
            "displayName": "Alan Turing",
            "state": "end",
            "joinedTime": "2026-01-05T08:00:50Z",
            "leftTime": "2026-01-05T08:05:00Z",
        },
    ]
}


def _status_handler(
    meeting: dict[str, object], participants: dict[str, object] | None = None
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/meetingParticipants":
            if participants is None:
                return httpx.Response(404, json={"message": "Not found", "trackingId": "T"})
            return httpx.Response(200, json=participants)
        return httpx.Response(200, json=meeting)

    return handler, seen


async def test_a_running_meeting_is_reported_as_counts_and_whether_the_agent_joined() -> None:
    handler, seen = _status_handler(MEETING_IN_PROGRESS, PARTICIPANTS)

    status = await _meetings(handler).meeting_status("m-1", "host@example.nl")

    assert status == MeetingStatus(
        CallState.IN_PROGRESS,
        participants=2,
        host_joined=True,
        started_at=datetime(2026, 1, 5, 8, 0, 40, tzinfo=UTC),
    )
    assert [r.url.path for r in seen] == ["/v1/meetings/m-1", "/v1/meetingParticipants"]
    assert all(r.method == "GET" for r in seen)
    assert all(r.url.params["hostEmail"] == "host@example.nl" for r in seen)
    assert seen[1].url.params["meetingId"] == "m-1"
    for personal in ("Ada", "Lovelace", "ada@example.org", "Grace", "mo@example.nl"):
        assert personal not in repr(status)


async def test_a_meeting_that_has_not_started_needs_no_participant_list() -> None:
    handler, seen = _status_handler({"id": "m-1", "state": "scheduled"})

    status = await _meetings(handler).meeting_status("m-1", "host@example.nl")

    assert status == MeetingStatus(CallState.SCHEDULED)
    assert [r.url.path for r in seen] == ["/v1/meetings/m-1"]


@pytest.mark.parametrize("state", ["ended", "missed", "expired"])
async def test_an_ended_meeting_has_nobody_in_it_and_the_last_leave_as_its_end(state: str) -> None:
    participants = {
        "items": [PARTICIPANTS["items"][0] | {"state": "end", "leftTime": "2026-01-05T08:29:00Z"}]
    }  # type: ignore[index]
    handler, _ = _status_handler({"id": "m-1", "state": state}, participants)

    status = await _meetings(handler).meeting_status("m-1", "host@example.nl")

    assert (status.state, status.participants, status.host_joined) == (CallState.ENDED, 0, True)
    assert status.ended_at == datetime(2026, 1, 5, 8, 29, tzinfo=UTC)


async def test_a_missing_participant_list_is_an_empty_one() -> None:
    handler, _ = _status_handler({"id": "m-1", "state": "ended"}, None)

    status = await _meetings(handler).meeting_status("m-1", "host@example.nl")

    assert status == MeetingStatus(CallState.ENDED)


async def test_a_refused_status_request_carries_webexs_reason() -> None:
    body = {"message": "Meeting not found.", "trackingId": "ROUTER_1"}
    meetings = _meetings(lambda request: httpx.Response(404, json=body))

    with pytest.raises(IntegrationError, match="rejected_404") as error:
        await meetings.meeting_status("m-1", "host@example.nl")

    assert error.value.reason == "Meeting not found."
    assert error.value.retryable is False


async def test_a_timestamp_without_zone_is_read_as_utc_not_as_local_time() -> None:
    participants = {
        "items": [{"coHost": True, "state": "joined", "joinedTime": "2026-01-05T08:00:40"}]
    }
    handler, _ = _status_handler(MEETING_IN_PROGRESS, participants)

    status = await _meetings(handler).meeting_status("m-1", "host@example.nl")

    assert status.started_at == datetime(2026, 1, 5, 8, 0, 40, tzinfo=UTC)
