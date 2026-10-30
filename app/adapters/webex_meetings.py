"""``VideoProvider`` on the Webex Meetings API (ADR 0017).

Meetings are scheduled by the Service App on behalf of the group's licensed host. Every
signed-in user of the organisation becomes cohost on joining (the agent); guests wait in the
lobby. Webex sends no invitations: OpenPlanner mails the citizen itself. The request carries
the group's name and times only (``app.domain.calls.meeting_request``). Start and end are
sent in the group's time zone: Webex refuses a ``timezone`` whose offset differs from the one
in the times (ADR 0065), so UTC times next to ``Europe/Amsterdam`` would never schedule.

Two more rules of the Meetings API, found with a direct call (ADR 0070): a time with
microseconds is refused ("format is wrong"), and so is a start more than about a minute in the
past ("before current time"), while a start a few seconds ago is accepted. The body therefore
carries whole seconds, and a start that has passed (a direct call, or its retry by the worker)
becomes the moment of the request. The booking keeps its own start.
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.adapters.webex_auth import AuthorizedClient
from app.domain.booking import MeetingRef
from app.domain.calls import MeetingRequest
from app.domain.ports import Clock, IntegrationError

MEETING_OPTIONS: dict[str, Any] = {
    "allowAnyUserToBeCoHost": True,
    "enabledJoinBeforeHost": False,
    "unlockedMeetingJoinSecurity": "allowJoinWithLobby",
    "sendEmail": False,
    "meetingOptions": {"enabledChat": True, "enabledVideo": True, "enabledFileTransfer": False},
    "attendeePrivileges": {"enabledChatHost": True, "enabledChatOtherParticipants": True},
}
"""Defaults for every meeting (ADR 0017: cohost for staff, lobby for guests, chat on)."""


class WebexMeetings:
    """Creates and deletes meetings.

    Args:
        client: Authorised API client.
        base_url: Webex API base, e.g. ``https://webexapis.com/v1``.
        clock: Current time; a start that has passed is moved to it.
    """

    def __init__(self, client: AuthorizedClient, base_url: str, clock: Clock) -> None:
        """Store the collaborators."""
        self._client = client
        self._base = base_url.rstrip("/")
        self._clock = clock

    async def create_meeting(self, request: MeetingRequest) -> MeetingRef:
        """Schedule the meeting on behalf of the group's host and return its id and link."""
        body = meeting_body(request, self._clock.now())
        response = await self._client.request(
            "POST", f"{self._base}/meetings", json=body, explain=True
        )
        data = response.json()
        if not isinstance(data.get("id"), str) or not isinstance(data.get("webLink"), str):
            raise IntegrationError("unexpected_response", retryable=False)
        return MeetingRef(data["id"], data["webLink"])

    async def delete_meeting(self, meeting_id: str, host_email: str) -> None:
        """Delete the meeting; one that no longer exists counts as deleted."""
        await self._client.request(
            "DELETE",
            f"{self._base}/meetings/{meeting_id}",
            params={"hostEmail": host_email, "sendEmail": "false"},
            ok=(200, 204, 404),
        )


def meeting_body(request: MeetingRequest, now: datetime) -> dict[str, Any]:
    """Return the ``POST /meetings`` body: the request in its own time zone plus the defaults.

    Times are sent in whole seconds, and a start before ``now`` becomes ``now`` (a direct
    call's meeting runs from the moment it is created). Shared with the group check, whose
    probe meeting must be exactly what a booking sends.
    """
    zone = ZoneInfo(request.timezone)
    start = max(request.start, now).replace(microsecond=0)
    end = request.end.replace(microsecond=0)
    return {
        "title": request.title,
        "start": start.astimezone(zone).isoformat(),
        "end": end.astimezone(zone).isoformat(),
        "timezone": request.timezone,
        "hostEmail": request.host_email,
        **MEETING_OPTIONS,
    }
