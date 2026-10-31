"""Inbound Webex and WxCC webhooks: signature checks and mapping to call events.

Signatures are checked over the raw body before anything is parsed (docs/webhooks.md):

- WxCC: ``X-WebexCC-Signature`` is hex HMAC-SHA256 of the body with the subscription secret;
  ``X-WebExCC-Timestamp`` must equal the body's ``comciscotimestamp`` and lie within 300 s.
- Webex: ``X-Spark-Signature`` is hex HMAC-SHA1 of the body with the webhook secret. Webex
  sends no signed event time, so there is no time window. Instead every event gets an
  ``event_key`` (a digest of resource, event, ``data.id`` and the join/leave time) that the
  domain remembers: a replayed or redelivered event is recorded once. Counts are capped at zero.

Only ids, event types, the host flag and failure reasons are read; participant names and
email addresses in the payloads are ignored.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.domain.calls import EventKind

MAX_SKEW = timedelta(seconds=300)

WXCC_EVENTS: dict[str, EventKind] = {
    "task:new": EventKind.TASK_CREATED,
    "task:connected": EventKind.TASK_CONNECTED,
    "task:ended": EventKind.TASK_ENDED,
    "task:failed": EventKind.TASK_FAILED,
}
"""WxCC event types OpenPlanner acts on. A booking's ``task:new`` is already recorded at
dispatch and ignored by the domain; the group check's probe work item needs it (ADR 0082)."""


@dataclass(frozen=True)
class ParsedEvent:
    """A verified webhook reduced to what OpenPlanner needs."""

    target_id: str
    kind: EventKind
    detail: str = ""
    event_key: str = ""
    """Identifies one delivery's event for deduplication; empty when there is nothing to key on."""


def event_body(payload: dict[str, Any]) -> bytes:
    """Serialise an outgoing test or simulated event exactly as it is signed."""
    return json.dumps(payload, separators=(",", ":")).encode()


def sign_webex(body: bytes, secret: str) -> dict[str, str]:
    """Return the headers Webex sends with ``body`` (self-test and simulator)."""
    return {"X-Spark-Signature": hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()}  # noqa: S324 - Webex's scheme


def sign_wxcc(body: bytes, secret: str, stamp_ms: int) -> dict[str, str]:
    """Return the headers WxCC sends with ``body``; ``stamp_ms`` is its ``comciscotimestamp``."""
    return {
        "X-WebexCC-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
        "X-WebexCC-Timestamp": str(stamp_ms),
    }


def verify_webex(body: bytes, signature: str | None, secret: str) -> bool:
    """Return whether ``signature`` is the HMAC-SHA1 of ``body`` under ``secret``."""
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()  # noqa: S324 - Webex's scheme
    return hmac.compare_digest(expected, signature.strip().lower())


def verify_wxcc(
    body: bytes, signature: str | None, timestamp: str | None, secret: str, now: datetime
) -> bool:
    """Return whether a WxCC webhook is authentic, untampered and recent."""
    if not signature or not timestamp:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip().lower()):
        return False
    try:
        payload = json.loads(body)
        sent_ms = int(timestamp)
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict) or str(payload.get("comciscotimestamp")) != timestamp:
        return False
    sent = datetime.fromtimestamp(sent_ms / 1000, tz=now.tzinfo)
    return abs(now - sent) <= MAX_SKEW


def parse_webex(payload: dict[str, Any]) -> ParsedEvent | None:
    """Map a Webex ``meetings`` or ``meetingParticipants`` event; ``None`` if irrelevant."""
    resource, event = payload.get("resource"), payload.get("event")
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    if resource == "meetings" and event in ("started", "ended"):
        meeting_id = data.get("meetingSeriesId") or data.get("id")
        kind = EventKind.MEETING_STARTED if event == "started" else EventKind.MEETING_ENDED
    elif resource == "meetingParticipants" and event in ("joined", "left"):
        meeting_id = data.get("meetingId")
        if event == "left":
            kind = EventKind.PARTICIPANT_LEFT
        elif data.get("host") is True or data.get("coHost") is True:
            kind = EventKind.HOST_JOINED
        else:
            kind = EventKind.GUEST_JOINED
    else:
        return None
    if not isinstance(meeting_id, str):
        return None
    return ParsedEvent(meeting_id, kind, event_key=_webex_key(str(resource), str(event), data))


def _webex_key(resource: str, event: str, data: dict[str, Any]) -> str:
    """Return a digest identifying one Webex event, or ``""`` without ``data.id``.

    A participant keeps its ``data.id`` when leaving and rejoining, so the join and leave
    times are part of the key; a replay of the same signed body gives the same key. Only ids
    and times go in, never names or addresses, and the digest is all that is stored.
    """
    event_id = data.get("id")
    if not isinstance(event_id, str):
        return ""
    parts = (
        resource,
        event,
        event_id,
        str(data.get("joinedTime", "")),
        str(data.get("leftTime", "")),
    )
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def parse_wxcc(payload: dict[str, Any]) -> ParsedEvent | None:
    """Map a WxCC task event; ``None`` if OpenPlanner does not act on it."""
    kind = WXCC_EVENTS.get(str(payload.get("type")))
    data = payload.get("data")
    if kind is None or not isinstance(data, dict):
        return None
    task_id = data.get("taskId")
    reason = data.get("reason") if kind is EventKind.TASK_FAILED else ""
    if not isinstance(task_id, str):
        return None
    return ParsedEvent(task_id, kind, str(reason or "")[:128])
