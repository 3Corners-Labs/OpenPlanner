"""Simulated Webex and WxCC events, played through OpenPlanner's own webhook endpoints.

For trying the flows without agents or platforms (never in production; ``SimulationService``
decides). Each event is built the way the platform sends it, signed with this installation's
webhook secret and posted in-process to ``/webhooks/webex`` or ``/webhooks/wxcc`` (an ASGI
transport on the running app). It takes the real path: signature, time window, parsing,
deduplication, timeline and call status. No tunnel or network is involved.

    task_connected   WxCC task:connected       an agent accepted the work item
    host_joined      Webex participant joined  the agent (host) is in the meeting
    guest_joined     Webex participant joined  the citizen is in the meeting
    participant_left Webex participant left
    meeting_started  Webex meeting started
    meeting_ended    Webex meeting ended
    task_failed      WxCC task:failed          nobody picked the work item up
    task_ended       WxCC task:ended
    trial_entered    Webex participant joined  the citizen is in the trial call (ADR 0071)
    trial_left       Webex participant left    the citizen left the trial call

The trial events carry the trial meeting's id, so the webhook matches them on the booking's
trial call and records them as the trial kinds (ADR 0076), never on the call status.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import httpx
from starlette.types import ASGIApp

from app.adapters.webhooks import event_body, sign_webex, sign_wxcc


class SimulatedEvent(StrEnum):
    """The events the simulator can play, in the order a call usually goes."""

    TASK_CONNECTED = "task_connected"
    HOST_JOINED = "host_joined"
    GUEST_JOINED = "guest_joined"
    PARTICIPANT_LEFT = "participant_left"
    MEETING_STARTED = "meeting_started"
    MEETING_ENDED = "meeting_ended"
    TASK_FAILED = "task_failed"
    TASK_ENDED = "task_ended"
    TRIAL_ENTERED = "trial_entered"
    TRIAL_LEFT = "trial_left"


WXCC_TYPES = {
    SimulatedEvent.TASK_CONNECTED: "task:connected",
    SimulatedEvent.TASK_FAILED: "task:failed",
    SimulatedEvent.TASK_ENDED: "task:ended",
}

TRIAL_EVENTS = {
    SimulatedEvent.TRIAL_ENTERED: SimulatedEvent.GUEST_JOINED,
    SimulatedEvent.TRIAL_LEFT: SimulatedEvent.PARTICIPANT_LEFT,
}
"""What Webex sends for a trial event: the same participant event, on the trial meeting."""


class PlayOutcome(StrEnum):
    """What happened to a played event (a code; the text is chosen where it is shown)."""

    DELIVERED = "delivered"
    NO_IDS = "no_ids"
    NO_SECRET = "no_secret"  # noqa: S105  # nosec B105 - an outcome code, not a secret
    REFUSED = "refused"


@dataclass(frozen=True)
class Played:
    """The outcome of one played event and the endpoint's HTTP status, if it answered."""

    event: SimulatedEvent
    outcome: PlayOutcome
    status: int | None = None


def webex_payload(event: SimulatedEvent, meeting_id: str, now: datetime) -> dict[str, Any]:
    """Return a Webex meetings or meetingParticipants event for ``meeting_id``.

    Every call gets fresh ids, so deduplication never drops a deliberate second click.
    """
    stamp = now.isoformat().replace("+00:00", "Z")
    if event in (SimulatedEvent.MEETING_STARTED, SimulatedEvent.MEETING_ENDED):
        name = "started" if event is SimulatedEvent.MEETING_STARTED else "ended"
        data: dict[str, Any] = {
            "id": f"{meeting_id}_I_{uuid.uuid4().hex[:12]}",
            "meetingSeriesId": meeting_id,
        }
        return {"id": uuid.uuid4().hex, "resource": "meetings", "event": name, "data": data}
    left = event is SimulatedEvent.PARTICIPANT_LEFT
    data = {
        "id": uuid.uuid4().hex,
        "meetingId": meeting_id,
        "host": event is SimulatedEvent.HOST_JOINED,
        ("leftTime" if left else "joinedTime"): stamp,
    }
    return {
        "id": uuid.uuid4().hex,
        "resource": "meetingParticipants",
        "event": "left" if left else "joined",
        "data": data,
    }


def wxcc_payload(event: SimulatedEvent, task_id: str, stamp_ms: int) -> dict[str, Any]:
    """Return a WxCC task event for ``task_id``."""
    data: dict[str, Any] = {"taskId": task_id}
    if event is SimulatedEvent.TASK_FAILED:
        data["reason"] = "simulated"
    return {"type": WXCC_TYPES[event], "comciscotimestamp": stamp_ms, "data": data}


async def play(
    app: ASGIApp,
    event: SimulatedEvent,
    *,
    meeting_id: str | None,
    task_id: str | None,
    trial_meeting_id: str | None = None,
    webex_secret: str | None,
    wxcc_secret: str | None,
    now: datetime,
) -> Played:
    """Build, sign and post ``event`` to the app's own webhook endpoint."""
    if event in WXCC_TYPES:
        if task_id is None:
            return Played(event, PlayOutcome.NO_IDS)
        if wxcc_secret is None:
            return Played(event, PlayOutcome.NO_SECRET)
        stamp = int(now.timestamp() * 1000)
        body = event_body(wxcc_payload(event, task_id, stamp))
        path, headers = "/webhooks/wxcc", sign_wxcc(body, wxcc_secret, stamp)
    else:
        sent, target = TRIAL_EVENTS.get(event, event), meeting_id
        if event in TRIAL_EVENTS:
            target = trial_meeting_id
        if target is None:
            return Played(event, PlayOutcome.NO_IDS)
        if webex_secret is None:
            return Played(event, PlayOutcome.NO_SECRET)
        body = event_body(webex_payload(sent, target, now))
        path, headers = "/webhooks/webex", sign_webex(body, webex_secret)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://simulator") as client:
        response = await client.post(
            path, content=body, headers=headers | {"Content-Type": "application/json"}
        )
    outcome = PlayOutcome.DELIVERED if response.status_code == 204 else PlayOutcome.REFUSED
    return Played(event, outcome, response.status_code)
