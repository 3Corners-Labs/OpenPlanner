"""``ContactCenterDispatcher`` on the WxCC Create Task API v2, work items (ADR 0018, 0021).

The work item goes to the group's business address and channel; the flow behind the entry
point chooses the queue from ``flowSettings.openplannerGroup``. ``workItemData`` carries the
booking reference, first name, start time, group name, meeting link and kind; nothing else
(CLAUDE.md). The asset's schema in Control Hub must define exactly these keys (runbook).

A cancelled booking's task is first marked with a Task Messages v2 append
(``cjp:task_write``) that sets ``kind`` to ``cancelled`` with only the booking reference, so
the agent sees it without the first name or the meeting link; then End Task v1 (``cjp:user``;
there is no v2 End Task) is tried as a secondary step (ADR 0021).

``read_task`` (ADR 0082 point 1) asks ``GET /v1/tasks/{taskId}`` (``cjp:config_read``) and
keeps only the task's status word and the time of its last change; the answer's attributes
and ``workItemData`` are never read. A 404 is a task WxCC does not know (``TaskState.NONE``).
"""

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from app.adapters.webex_auth import AuthorizedClient
from app.domain.calls import TaskSnapshot, TaskState, WorkItem
from app.domain.ports import Clock, IntegrationError

ROUTING_KEY = "openplannerGroup"

PROBE_KIND = "probe"
"""``kind`` of the group check's probe work item (ADR 0082 point 2); the flow's Case branch on
it ends the probe before an agent sees it. Fixed on purpose: the flow and OpenPlanner agree."""

TASK_STATES: dict[str, TaskState] = {
    "new": TaskState.QUEUED,
    "created": TaskState.QUEUED,
    "queued": TaskState.QUEUED,
    "parked": TaskState.QUEUED,
    "offered": TaskState.QUEUED,
    "connected": TaskState.CONNECTED,
    "active": TaskState.CONNECTED,
    "hold": TaskState.CONNECTED,
    "consult": TaskState.CONNECTED,
    "wrapup": TaskState.ENDED,
    "ended": TaskState.ENDED,
    "completed": TaskState.ENDED,
    "closed": TaskState.ENDED,
    "abandoned": TaskState.ENDED,
    "failed": TaskState.FAILED,
}
"""WxCC's status words, lower case, to OpenPlanner's task states; an unknown word is
``TaskState.NONE`` with the word kept in ``TaskSnapshot.status``."""

WORK_ITEM_FIELDS = (
    "bookingReference",
    "firstName",
    "startTime",
    "groupName",
    "meetingLink",
    "kind",
)
"""The ``workItemData`` keys OpenPlanner sends; the asset schema must define exactly these."""
REQUIRED_FIELDS = frozenset({"bookingReference"})
"""Keys that may be required in the asset schema: a cancellation message carries only these
and ``kind``."""


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

    async def read_task(self, task_id: str) -> TaskSnapshot:
        """Return the task's state (``GET /v1/tasks/{taskId}``); ``NONE`` for a 404."""
        url = f"{self._base}/v1/tasks/{quote(task_id, safe='')}"
        try:
            response = await self._client.request("GET", url, ok=(200,))
        except IntegrationError as error:
            if error.code == "rejected_404":
                return TaskSnapshot(TaskState.NONE)
            raise
        return task_snapshot(response.json())

    def _task_url(self, version: str, task_id: str, action: str) -> str:
        """Return a task action URL; the id is escaped so it cannot change the path."""
        return f"{self._base}/{version}/tasks/{quote(task_id, safe='')}/{action}"


def task_snapshot(payload: object) -> TaskSnapshot:
    """Reduce a task answer to its status word and last change; nothing else is read."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return TaskSnapshot(TaskState.NONE)
    status = str(data.get("status") or "").strip().lower()[:32]
    changed = data.get("lastUpdatedTime") or data.get("endedTime") or data.get("createdTime")
    changed_at = (
        datetime.fromtimestamp(changed / 1000, tz=UTC)
        if isinstance(changed, int | float) and changed > 0
        else None
    )
    return TaskSnapshot(TASK_STATES.get(status, TaskState.NONE), status, changed_at)
