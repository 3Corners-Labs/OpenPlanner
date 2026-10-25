"""``ContactCenterDispatcher`` on the WxCC Create Task API v2, work items (ADR 0018, 0020).

The work item goes to the group's business address and channel; the flow behind the entry
point chooses the queue from ``flowSettings.openplannerGroup``. ``workItemData`` carries the
booking reference, first name, start time, group name, meeting link and kind; nothing else
(CLAUDE.md). The asset's schema in Control Hub must define exactly these keys (runbook).

A cancelled booking's task is ended with End Task (``cjp:user``); when WxCC refuses, a Task
Messages append (``cjp:task_write``) sets ``kind`` to ``cancelled`` with only the booking
reference, so the agent sees it without the first name or the meeting link (ADR 0020).
"""

import uuid
from typing import Any
from urllib.parse import quote

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


def cancellation_body(reference: str, alias_id: str, timestamp_ms: int) -> dict[str, Any]:
    """Return the Task Messages v2 request that marks a work item cancelled."""
    return {
        "mediaType": "workItem",
        "channelParams": {
            "type": "work-item-form",
            "message": {
                "aliasId": alias_id,
                "workItemData": {"bookingReference": reference, "kind": "cancelled"},
                "timestamp": timestamp_ms,
            },
        },
    }


class WxccTasks:
    """Creates, ends and annotates work items.

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

    async def end_task(self, task_id: str) -> None:
        """End the task (``POST /v1/tasks/{taskId}/end``)."""
        await self._client.request("POST", self._task_url("v1", task_id, "end"), ok=(200, 202, 204))

    async def mark_cancelled(self, task_id: str, reference: str) -> None:
        """Append the cancellation to the task (``POST /v2/tasks/{taskId}/messages``)."""
        timestamp = int(self._clock.now().timestamp() * 1000)
        await self._client.request(
            "POST",
            self._task_url("v2", task_id, "messages"),
            json=cancellation_body(reference, str(uuid.uuid4()), timestamp),
            ok=(200, 202),
        )

    def _task_url(self, version: str, task_id: str, action: str) -> str:
        """Return a task action URL; the id is escaped so it cannot change the path."""
        return f"{self._base}/{version}/tasks/{quote(task_id, safe='')}/{action}"
