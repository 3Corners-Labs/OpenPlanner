import json
from datetime import timedelta

from app.adapters.webhooks import (
    WEBEX_INBOUND,
    WXCC_INBOUND,
    InboundWebhook,
    check_webex,
    check_wxcc,
    parse_webex,
    parse_wxcc,
)
from app.domain.calls import EventKind
from app.domain.inbound_gate import Refusal
from tests.support.webhooks import NOW, SECRET, sign, timestamp, wxcc_body


def test_wxcc_signature_timestamp_and_window() -> None:
    body = wxcc_body()

    assert check_wxcc(body, sign(body), timestamp(body), SECRET, NOW) is None
    assert (
        check_wxcc(body, sign(body).upper(), timestamp(body), SECRET, NOW + timedelta(minutes=4))
        is None
    )
    late = NOW + timedelta(minutes=6)
    assert check_wxcc(body, sign(body), timestamp(body), SECRET, late) is not None
    assert check_wxcc(body, sign(body), "1", SECRET, NOW)  # header differs from body
    assert check_wxcc(body + b" ", sign(body), timestamp(body), SECRET, NOW)  # tampered
    assert check_wxcc(body, None, timestamp(body), SECRET, NOW)
    assert check_wxcc(body, sign(body), None, SECRET, NOW)
    assert check_wxcc(b"not json", sign(b"not json"), "1", SECRET, NOW)


def test_a_stale_time_is_told_apart_from_a_wrong_signature() -> None:
    """ADR 0124 point 2 f and g: the signature is checked first, then the time."""
    body = wxcc_body()
    late = NOW + timedelta(minutes=6)

    assert check_wxcc(body, sign(body), timestamp(body), SECRET, NOW) is None
    assert check_wxcc(body, sign(body), timestamp(body), SECRET, late) is Refusal.STALE
    # A wrong signature is a wrong signature, however old the body is.
    assert check_wxcc(body, "0" * 64, timestamp(body), SECRET, late) is Refusal.SIGNATURE
    assert check_wxcc(body, None, timestamp(body), SECRET, NOW) is Refusal.SIGNATURE
    assert check_wxcc(body, sign(body), None, SECRET, NOW) is Refusal.SIGNATURE
    assert check_wxcc(body, sign(body), "1", SECRET, NOW) is Refusal.SIGNATURE
    assert check_wxcc(body, sign(body), "x", SECRET, NOW) is Refusal.SIGNATURE
    listing = b"[1]"
    assert check_wxcc(listing, sign(listing), "1", SECRET, NOW) is Refusal.SIGNATURE


def test_an_impossible_time_is_not_authentic() -> None:
    body = json.dumps({"comciscotimestamp": 10**30}).encode()

    assert check_wxcc(body, sign(body), str(10**30), SECRET, NOW) is Refusal.SIGNATURE


def test_webex_headers_are_checked_by_signature_only() -> None:
    body = b'{"resource": "meetings"}'

    assert check_webex(body, {"x-spark-signature": sign(body, "sha1")}, SECRET, NOW) is None
    assert check_webex(body, {"x-spark-signature": "bad"}, SECRET, NOW) is Refusal.SIGNATURE
    assert check_webex(body, {}, SECRET, NOW) is Refusal.SIGNATURE


def test_webex_signature() -> None:
    body = b'{"resource": "meetings"}'

    assert check_webex(body, {"x-spark-signature": sign(body, "sha1")}, SECRET, NOW) is None
    assert check_webex(body, {"x-spark-signature": sign(body, "sha1").upper()}, SECRET, NOW) is None
    assert check_webex(body, {"x-spark-signature": sign(body, "sha256")}, SECRET, NOW)
    assert check_webex(body, {}, SECRET, NOW) is Refusal.SIGNATURE


def test_every_platform_has_the_same_record() -> None:
    """ADR 0124: the video platform is an ``InboundWebhook`` like the contact-center ones."""
    assert WEBEX_INBOUND.source == "webex"
    assert (
        WEBEX_INBOUND.event_type({"resource": "meetings", "event": "started"}) == "meetings:started"
    )
    assert WEBEX_INBOUND.parse({"resource": "other", "data": {}}) is None
    assert isinstance(WXCC_INBOUND, InboundWebhook)
    assert WXCC_INBOUND.registered_path == "/webhooks/wxcc"


def test_wxcc_events_map_to_call_events() -> None:
    connected = parse_wxcc(json.loads(wxcc_body()))
    failed = parse_wxcc(json.loads(wxcc_body("task:failed", reason="ENTRY_POINT_NOT_FOUND")))

    assert connected is not None
    assert (connected.target_id, connected.kind) == ("task-1", EventKind.TASK_CONNECTED)
    assert failed is not None
    assert (failed.kind, failed.detail) == (EventKind.TASK_FAILED, "ENTRY_POINT_NOT_FOUND")
    created = parse_wxcc(json.loads(wxcc_body("task:new")))
    assert created is not None
    assert created.kind is EventKind.TASK_CREATED  # ADR 0082: the probe work item needs it
    assert parse_wxcc({"type": "task:ended", "data": {}}) is None


def test_webex_events_map_to_call_events_without_names() -> None:
    started = parse_webex(
        {
            "resource": "meetings",
            "event": "started",
            "data": {"id": "m-1_I_7", "meetingSeriesId": "m-1"},
        }
    )
    host = parse_webex(
        {
            "resource": "meetingParticipants",
            "event": "joined",
            "data": {"meetingId": "m-1", "coHost": True, "displayName": "Agent", "email": "a@x.nl"},
        }
    )
    guest = parse_webex(
        {
            "resource": "meetingParticipants",
            "event": "joined",
            "data": {"meetingId": "m-1", "host": False},
        }
    )
    left = parse_webex(
        {"resource": "meetingParticipants", "event": "left", "data": {"meetingId": "m-1"}}
    )

    assert started is not None
    assert (started.target_id, started.kind) == ("m-1", EventKind.MEETING_STARTED)
    assert host is not None
    assert host.kind is EventKind.HOST_JOINED
    assert guest is not None
    assert guest.kind is EventKind.GUEST_JOINED
    assert left is not None
    assert left.kind is EventKind.PARTICIPANT_LEFT
    assert "Agent" not in repr(host)
    assert parse_webex({"resource": "recordings", "event": "created", "data": {}}) is None
    assert parse_webex({"resource": "meetings", "event": "started", "data": "x"}) is None


def _participant(event: str, **data: object) -> dict[str, object]:
    return {
        "id": "webhook-1",
        "resource": "meetingParticipants",
        "event": event,
        "data": {"id": "m-1_I_7_p-9", "meetingId": "m-1", **data},
    }


def test_webex_event_key_identifies_one_event_without_personal_data() -> None:
    joined = parse_webex(_participant("joined", joinedTime="2026-01-05T08:01:00Z", email="a@x.nl"))
    replay = parse_webex(_participant("joined", joinedTime="2026-01-05T08:01:00Z", email="a@x.nl"))
    left = parse_webex(_participant("left", leftTime="2026-01-05T08:20:00Z"))
    rejoined = parse_webex(_participant("joined", joinedTime="2026-01-05T08:21:00Z"))
    no_id = parse_webex(
        {"resource": "meetingParticipants", "event": "joined", "data": {"meetingId": "m-1"}}
    )

    assert joined is not None
    assert replay is not None
    assert left is not None
    assert rejoined is not None
    assert no_id is not None
    assert joined.event_key == replay.event_key
    assert len({joined.event_key, left.event_key, rejoined.event_key}) == 3
    assert len(joined.event_key) == 64
    assert "a@x.nl" not in joined.event_key
    assert no_id.event_key == ""  # nothing to deduplicate on


def test_a_wxcc_event_is_keyed_for_deduplication() -> None:
    """ADR 0082 point 5: a replay of the same body has the same key; another event another."""
    first = parse_wxcc(json.loads(wxcc_body("task:connected")))
    again = parse_wxcc(json.loads(wxcc_body("task:connected")))
    other = parse_wxcc(json.loads(wxcc_body("task:ended")))
    assert first is not None
    assert again is not None
    assert other is not None
    assert first.event_key == again.event_key != other.event_key
    assert len(first.event_key) == 64
    without_id = {"type": "task:ended", "data": {"taskId": "t", "createdTime": 1}}
    unkeyed = parse_wxcc(without_id)  # a time could be the task's own: no key without an id
    assert unkeyed is not None
    assert unkeyed.event_key == ""
    bare = parse_wxcc({"type": "task:ended", "data": {"taskId": "t"}})
    assert bare is not None
    assert bare.event_key == ""
