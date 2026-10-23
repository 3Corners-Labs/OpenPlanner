"""``ContactCenterDispatcher`` on the WxCC Create Task API v2, work items (ADR 0018).

The work item goes to the group's business address and channel; the flow behind the entry
point chooses the queue from ``flowSettings.openplannerGroup``. ``workItemData`` carries the
booking reference, first name, start time, group name, meeting link and kind; nothing else
(CLAUDE.md). The asset's schema in Control Hub must define exactly these keys (runbook).
"""

import uuid
from typing import Any

from app.adapters.webex_auth import AuthorizedClient
from app.domain.calls import WorkItem
from app.domain.ports import Clock, IntegrationError

ROUTING_KEY = "openplannerGroup"


def task_body(item: WorkItem, alias_id: str, timestamp_ms: int) -> dict[str, Any]:
    """Return the Create Task v2 request for ``item``."""
    return {
        "origin": {"id": item.reference, "name": item.first_name},
        "destination": {"id": item.business_address, "type": "businessAddress"},
        "channelType": "workItem",
        "channel": item.channel,
        "flowSettings": {ROUTING_KEY: item.routing_value},
        "channelParams": {
            "type": "work-item-form",
            "message": {
                "aliasId": alias_id,
                "workItemData": {
                    "bookingReference": item.reference,
                    "firstName": item.first_name,
                    "startTime": item.start_label,
                    "groupName": item.group_name,
                    "meetingLink": item.meeting_link,
                    "kind": item.kind,
                },
                "timestamp": timestamp_ms,
            },
        },
    }


class WxccTasks:
    """Creates work items.

    Args:
        client: Authorised API client.
        base_url: WxCC API host of the organisation's region.
        clock: Current time, for the work item timestamp.
    """

    def __init__(self, client: AuthorizedClient, base_url: str, clock: Clock) -> None:
        """Store the collaborators."""
        self._client = client
        self._base = base_url.rstrip("/")
        self._clock = clock

    async def create_task(self, item: WorkItem) -> str:
        """Create the work item and return its task id."""
        timestamp = int(self._clock.now().timestamp() * 1000)
        body = task_body(item, str(uuid.uuid4()), timestamp)
        response = await self._client.request(
            "POST", f"{self._base}/v2/tasks", json=body, ok=(200, 201, 202)
        )
        task_id = response.json().get("data", {}).get("id")
        if not isinstance(task_id, str):
            raise IntegrationError("unexpected_response", retryable=False)
        return task_id
