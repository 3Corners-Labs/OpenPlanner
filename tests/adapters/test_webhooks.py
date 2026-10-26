import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

from app.adapters.webhooks import parse_webex, parse_wxcc, verify_webex, verify_wxcc
from app.domain.calls import EventKind

SECRET = "webhook-secret"
NOW = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


def wxcc_body(event_type: str = "task:connected", at: datetime = NOW, **data: object) -> bytes:
    ms = int(at.timestamp() * 1000)
    return json.dumps(
        {
            "id": "4c2c4890",
            "specversion": "1.0",
            "type": event_type,
            "comciscotimestamp": ms,
            "data": {"taskId": "task-1", **data},
        }
    ).encode()


def sign(body: bytes, algorithm: str = "sha256") -> str:
    return hmac.new(SECRET.encode(), body, getattr(hashlib, algorithm)).hexdigest()


def timestamp(body: bytes) -> str:
    return str(json.loads(body)["comciscotimestamp"])


def test_wxcc_signature_timestamp_and_window() -> None:
    body = wxcc_body()

    assert verify_wxcc(body, sign(body), timestamp(body), SECRET, NOW)
    assert verify_wxcc(
        body, sign(body).upper(), timestamp(body), SECRET, NOW + timedelta(minutes=4)
    )
    assert not verify_wxcc(body, sign(body), timestamp(body), SECRET, NOW + timedelta(minutes=6))
    assert not verify_wxcc(body, sign(body), "1", SECRET, NOW)  # header differs from body
    assert not verify_wxcc(body + b" ", sign(body), timestamp(body), SECRET, NOW)  # tampered
    assert not verify_wxcc(body, None, timestamp(body), SECRET, NOW)
    assert not verify_wxcc(body, sign(body), None, SECRET, NOW)
    assert not verify_wxcc(b"not json", sign(b"not json"), "1", SECRET, NOW)


def test_webex_signature() -> None:
    body = b'{"resource": "meetings"}'

    assert verify_webex(body, sign(body, "sha1"), SECRET)
    assert not verify_webex(body, sign(body, "sha256"), SECRET)
    assert not verify_webex(body, None, SECRET)


def test_wxcc_events_map_to_call_events() -> None:
    connected = parse_wxcc(json.loads(wxcc_body()))
    failed = parse_wxcc(json.loads(wxcc_body("task:failed", reason="ENTRY_POINT_NOT_FOUND")))

    assert connected is not None
    assert (connected.target_id, connected.kind) == ("task-1", EventKind.TASK_CONNECTED)
    assert failed is not None
    assert (failed.kind, failed.detail) == (EventKind.TASK_FAILED, "ENTRY_POINT_NOT_FOUND")
    assert parse_wxcc(json.loads(wxcc_body("task:new"))) is None
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
