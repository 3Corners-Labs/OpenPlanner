"""``VideoProvider`` on the Webex Meetings API (ADR 0017).

Meetings are scheduled by the Service App on behalf of the group's licensed host. Every
signed-in user of the organisation becomes cohost on joining (the agent); guests wait in the
lobby. Webex sends no invitations: OpenPlanner mails the citizen itself. The request carries
the group's name and times only (``app.domain.calls.meeting_request``).
"""

from typing import Any

from app.adapters.webex_auth import AuthorizedClient
from app.domain.booking import MeetingRef
from app.domain.calls import MeetingRequest
from app.domain.ports import IntegrationError

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
    """

    def __init__(self, client: AuthorizedClient, base_url: str) -> None:
        """Store the collaborators."""
        self._client = client
        self._base = base_url.rstrip("/")

    async def create_meeting(self, request: MeetingRequest) -> MeetingRef:
        """Schedule the meeting on behalf of the group's host and return its id and link."""
        body = {
            "title": request.title,
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "timezone": request.timezone,
            "hostEmail": request.host_email,
            **MEETING_OPTIONS,
        }
        response = await self._client.request("POST", f"{self._base}/meetings", json=body)
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
