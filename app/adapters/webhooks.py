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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pydantic import SecretStr

from app.config import Settings
from app.domain.booking import booking_id_of
from app.domain.calls import EventKind
from app.domain.connection_health import Platform
from app.domain.contact_center import ContactCenterKind
from app.domain.inbound_gate import Refusal

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
    origin: str = field(default="", repr=False)
    """The booking reference OpenPlanner sent as the task's origin, if the event carries one:
    matches a late task to its attempt (ADR 0121 step 6b). Anything else in the origin (the name
    beside it, or a caller's address on a task OpenPlanner did not make) is never kept."""


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


def check_webex(
    body: bytes, headers: Mapping[str, str], secret: str, now: datetime
) -> Refusal | None:
    """Check a Webex webhook by its ``X-Spark-Signature``; ``now`` is unused (no signed time).

    Returns:
        ``None`` when authentic, else ``Refusal.SIGNATURE``.
    """
    if verify_webex(body, headers.get("x-spark-signature"), secret):
        return None
    return Refusal.SIGNATURE


def check_wxcc(
    body: bytes, signature: str | None, timestamp: str | None, secret: str, now: datetime
) -> Refusal | None:
    """Check a WxCC webhook: authentic and untampered first, then recent.

    The signature is compared first, in constant time; only a body that verifies has its time
    looked at. A body that is not a JSON object, or whose ``comciscotimestamp`` differs from the
    header, is never authentic.

    Returns:
        ``None`` when the webhook is good, ``Refusal.SIGNATURE`` when it is not authentic or
        the time claims do not match, ``Refusal.STALE`` when it is authentic but more than
        ``MAX_SKEW`` from ``now``.
    """
    if not signature or not timestamp:
        return Refusal.SIGNATURE
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip().lower()):
        return Refusal.SIGNATURE
    try:
        payload = json.loads(body)
        sent_ms = int(timestamp)
    except (ValueError, TypeError):
        return Refusal.SIGNATURE
    if not isinstance(payload, dict) or str(payload.get("comciscotimestamp")) != timestamp:
        return Refusal.SIGNATURE
    try:
        sent = datetime.fromtimestamp(sent_ms / 1000, tz=now.tzinfo)
    except (OverflowError, OSError, ValueError):
        return Refusal.SIGNATURE
    return None if abs(now - sent) <= MAX_SKEW else Refusal.STALE


def verify_wxcc(
    body: bytes, signature: str | None, timestamp: str | None, secret: str, now: datetime
) -> bool:
    """Return whether a WxCC webhook is authentic, untampered and recent."""
    return check_wxcc(body, signature, timestamp, secret, now) is None


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
    key = _wxcc_key(payload, task_id)
    return ParsedEvent(task_id, kind, str(reason or "")[:128], event_key=key, origin=_origin(data))


def _origin(data: dict[str, Any]) -> str:
    """Return the origin id of a task event (``origin`` or ``origin.id``), at most 32 characters.

    Only the id is read: Create Task sends ``{"id": reference, "name": first name}``, and the
    name is personal data that OpenPlanner never takes back. A task the client's contact center
    made itself may carry a caller's address or number as its origin; anything that is not a
    booking reference is dropped here, so it is never held.
    """
    found = data.get("origin")
    if isinstance(found, dict):
        found = found.get("id")
    if not isinstance(found, str) or booking_id_of(found) is None:
        return ""
    return found


def _wxcc_key(payload: dict[str, Any], task_id: str) -> str:
    """Return a digest identifying one WxCC event (ADR 0082 point 5), or ``""``.

    A WxCC event is a CloudEvent with an ``id`` of its own; a redelivery or replay of the same
    signed body carries the same id. The type and task go in too, so an id reused across event
    types never hides one. Without an id there is no key: a time field could be the task's own
    and would hide a real second event (a second ``task:connected`` after a transfer). Only
    ids and types go in, and the digest is all that is stored.
    """
    event_id = payload.get("id")
    if not isinstance(event_id, str) or not event_id:
        return ""
    parts = ("wxcc", str(payload.get("type")), task_id, event_id)
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


@dataclass(frozen=True)
class InboundWebhook:
    """How one contact-center platform's webhooks are checked and read (ADR 0032 point 6).

    The route ``/webhooks/contact-center/{kind}`` is the same for every platform; what differs
    is here, defined by the platform's adapter and listed in ``contact_centers.INBOUND``.

    Attributes:
        kind: The platform.
        platform: The connection whose latest inbound event the webhook is noted as.
        source: Short code for logs, security events and the event keys that recognise a
            replay (``wxcc`` stays the code of Webex Contact Center, so the keys stored before
            ADR 0032 keep matching).
        secret: Reads the webhook secret from the settings; without it the endpoint does not
            exist.
        verify: ``(body, headers, secret, now)`` → ``None`` when authentic, untampered and within
            300 s, else ``Refusal.SIGNATURE`` or ``Refusal.STALE`` (ADR 0124 point 2).
        parse: The verified JSON object → the event, or ``None`` when OpenPlanner ignores it.
        event_type: The verified JSON object → its event type, codes only, for the note.
        alias: An older address that still works and where OpenPlanner still registers, until
            the subscriptions move (CC-4); ``None`` for a platform without one.
    """

    kind: ContactCenterKind
    platform: Platform
    source: str
    secret: Callable[[Settings], SecretStr | None]
    verify: Callable[[bytes, Mapping[str, str], str, datetime], Refusal | None]
    parse: Callable[[dict[str, Any]], ParsedEvent | None]
    event_type: Callable[[dict[str, Any]], str]
    alias: str | None = None

    @property
    def path(self) -> str:
        """Return the platform's address: ``/webhooks/contact-center/{kind}``."""
        return f"{CONTACT_CENTER_PATH}/{self.kind.value}"

    @property
    def registered_path(self) -> str:
        """Return where OpenPlanner registers its subscription: the alias while there is one."""
        return self.alias or self.path

    def organisation_url(self, base: str, organisation: str) -> str:
        """Return an organisation's own address at ``base`` (``Settings.webhook_base``)."""
        return f"{base}{self.organisation_path(organisation)}"

    def organisation_path(self, organisation: str) -> str:
        """Return an organisation's own address (ADR 0032 point 10): ``{path}/{organisation}``."""
        return f"{self.path}/{organisation}"

    @property
    def paths(self) -> tuple[str, ...]:
        """Return every address that is OpenPlanner's for this platform."""
        return (self.path,) if self.alias is None else (self.path, self.alias)


CONTACT_CENTER_PATH = "/webhooks/contact-center"
"""The prefix of every contact-center platform's webhook address (ADR 0032 point 6)."""


def _verify_wxcc_headers(
    body: bytes, headers: Mapping[str, str], secret: str, now: datetime
) -> Refusal | None:
    """Check a WxCC webhook by its ``X-WebexCC-Signature`` and ``X-WebexCC-Timestamp``."""
    return check_wxcc(
        body,
        headers.get("x-webexcc-signature"),
        headers.get("x-webexcc-timestamp"),
        secret,
        now,
    )


def _wxcc_type(payload: dict[str, Any]) -> str:
    """Return a WxCC event's type (codes only, capped)."""
    return str(payload.get("type", ""))[:64]


def _wxcc_secret(settings: Settings) -> SecretStr | None:
    """Return the WxCC subscription secret (``WXCC_WEBHOOK_SECRET``)."""
    return settings.wxcc_webhook_secret


WXCC_INBOUND = InboundWebhook(
    kind=ContactCenterKind.WEBEX_CC,
    platform=Platform.WXCC,
    source="wxcc",
    secret=_wxcc_secret,
    verify=_verify_wxcc_headers,
    parse=parse_wxcc,
    event_type=_wxcc_type,
    alias="/webhooks/wxcc",
)
"""Webex Contact Center's webhooks: ``/webhooks/contact-center/webex_cc`` and ``/webhooks/wxcc``."""
