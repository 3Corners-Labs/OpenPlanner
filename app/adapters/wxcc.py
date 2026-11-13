"""``ContactCenterDispatcher`` on the WxCC Create Task API v2, work items (ADR 0018, 0021).

The adapter of ``ContactCenterKind.WEBEX_CC`` (ADR 0032): ``DESCRIPTOR`` describes the group
routing WxCC needs (business address and channel, both required, and a routing value of at most
512 bytes), and ``task_body`` maps the neutral ``ContactCenterTask`` to Create Task v2.

The work item goes to the group's business address and channel; the flow behind the entry
point chooses the queue from ``flowSettings.openplannerGroup``. The routing variables of
ADR 0081 (mapped subject and attribute codes) go next to it in ``flowSettings``, where the
flow reads them as flow variables; ``openplannerGroup`` is reserved and always wins. The
contact-center codes of the skills a booking requires (ADR 0121 step 4b) go in
``openplannerSkills``, comma-separated, only when it requires any, and also win over a routing
variable of that name.
``workItemData`` carries the booking reference, first name, start time, group name, meeting
link and kind; nothing else (CLAUDE.md). The asset's schema in Control Hub must define
exactly these keys (runbook).

The adapter declares what it carries on the channel-independent task contract (ADR 0121 step
6): the interaction kinds ``video`` (the work item carries the host join URL) and ``none`` (a work
item without a meeting, step 6b), and skill codes. A task of another kind is sent with an empty
``meetingLink`` and its kind in the reserved flow variable ``openplannerInteraction``; a video
task has no such key, so the body of every existing flow is byte for byte what it was
(``tests/adapters/wxcc_task_bodies.json``). A task of a kind not carried is refused before
anything is sent.

A conversation task (ADR 0123 point 8, ``TaskKind.CONVERSATION``) is a ``customMessaging`` task
instead of a work item: Create Task v2 with the same business address, channel and flow settings
(routing variables, skills, interaction), the booking reference as the participant's identifier
(WxCC allows one open conversation per identifier and address, so one per appointment) and the
first message as a text. ``append_message`` adds the participant's later messages with Task
Messages v2, the message identifier being the ``aliasId`` so a retry is not a second message.
The first message is composed at the edge from the catalogue and holds only the fields CLAUDE.md
allows in an outbound payload; ``conversation_body`` is the one place that puts it in a request.
WxCC answers 403 while its messaging feature or channel is not set up for the organisation and
refuses a second open conversation for the same identifier: the first is read as
``messaging_not_configured`` (its status kept as the reason, since a 403 has more than one
cause), the second as ``conversation_open``, never a form task instead.

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
from typing import Any, NamedTuple
from urllib.parse import quote

import httpx

from app.adapters.webex_auth import (
    CREDENTIAL_NAME,
    AuthorizedClient,
    ServiceAppConfig,
    TokenStore,
    WebexTokens,
)
from app.domain import calls
from app.domain.calls import (
    CONVERSATION_OPEN,
    MESSAGE_TEXT_MAX_LENGTH,
    MESSAGING_NOT_CONFIGURED,
    ContactCenterTask,
    ParticipantMessage,
    TaskSnapshot,
    TaskState,
)
from app.domain.contact_center import (
    AttributeSpec,
    ContactCenterCapabilities,
    ContactCenterKind,
    ContactCenterRouting,
    ContactCenterTaskRef,
    ProviderDescriptor,
    TaskKind,
    uncarried,
)
from app.domain.interaction import InteractionKind
from app.domain.ports import Clock, IntegrationError, RequestLog
from app.domain.security_log import SecurityLog

CONVERSATION_CHANNEL_TYPE = "customMessaging"
"""WxCC's name of a conversation task: the ``channelType`` of Create Task v2 and the
``mediaType`` of Task Messages v2."""

ROUTING_KEY = "openplannerGroup"
SKILLS_KEY = "openplannerSkills"
"""The flow variable of the contact-center codes of the skills a booking requires (ADR 0121 step
4b), comma-separated and sorted; absent when it requires none. Written after the routing
variables of ADR 0081, so none of them can stand in for it."""

INTERACTION_KEY = "openplannerInteraction"
"""The flow variable of a task's interaction kind (ADR 0121 step 6b), written only for a kind
other than ``video`` and after the routing variables, so none of them can stand in for it."""

BUSINESS_ADDRESS = "business_address"
"""Routing attribute: the business address of the work item asset (ADR 0018)."""
CHANNEL = "channel"
"""Routing attribute: the name of the work item channel."""

DESCRIPTOR = ProviderDescriptor(
    kind=ContactCenterKind.WEBEX_CC,
    label_key="admin.contact_center.webex_cc",
    attributes=(
        AttributeSpec(BUSINESS_ADDRESS, 255, True, "admin.field.wxcc_business_address"),
        AttributeSpec(CHANNEL, 255, True, "admin.field.wxcc_channel"),
    ),
    routing_value_max_length=512,
    routing_value_label_key="admin.field.wxcc_routing_value",
    routing_value_hint_key="admin.field.wxcc_routing_value_hint",
    capabilities=ContactCenterCapabilities(
        interaction_kinds=frozenset({InteractionKind.VIDEO, InteractionKind.NONE}),
        skills=True,
        task_kinds=frozenset({TaskKind.FORM, TaskKind.CONVERSATION}),
    ),
)
"""What a group needs for WxCC (ADR 0032 point 4) and what WxCC carries (ADR 0121 step 6); the
limits are WxCC's (``flowSettings`` values up to 512 bytes), fixed by the platform and so
constants, not settings. Video and ``none`` are carried (``none`` since ADR 0121 step 6b: a work
item with an empty ``meetingLink``); a telephone interaction would need the participant's number,
which never leaves OpenPlanner. The conversation (a ``customMessaging`` task) is carried since
ADR 0123 step 7a; whether an organisation's tenant has the messaging feature is not known here:
WxCC answers 403 when it has not."""

PROBE_KIND = calls.PROBE_KIND
"""``kind`` of the group check's probe work item (ADR 0082 point 2), defined in the domain."""

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


class WxccMapping(NamedTuple):
    """A group's WxCC attributes; either may be missing while the routing is incomplete."""

    channel: str | None
    business_address: str | None

    @property
    def complete(self) -> tuple[str, str] | None:
        """The channel and business address when both are set, else ``None``."""
        if self.channel and self.business_address:
            return self.channel, self.business_address
        return None


def wxcc_mapping(routing: ContactCenterRouting | None) -> WxccMapping | None:
    """Return the WxCC attributes of ``routing``; ``None`` without one or for another platform.

    The one place that checks the kind and reads the WxCC attributes of a routing.
    """
    if routing is None or routing.kind is not ContactCenterKind.WEBEX_CC:
        return None
    return WxccMapping(routing.attribute(CHANNEL), routing.attribute(BUSINESS_ADDRESS))


def channel_and_address(routing: ContactCenterRouting | None) -> tuple[str, str] | None:
    """Return the channel and business address of a WxCC routing that has both, else ``None``."""
    mapping = wxcc_mapping(routing)
    return mapping.complete if mapping else None


def _flow_settings(item: ContactCenterTask) -> dict[str, str]:
    """Return the ``flowSettings`` of ``item``: routing variables, skills, interaction, route.

    The one place they are built, so a work item and a conversation task carry the same.
    """
    return {
        **{
            name: value
            for name, value in item.variables
            if name not in (SKILLS_KEY, INTERACTION_KEY)
        },
        **({SKILLS_KEY: ",".join(item.skills)} if item.skills else {}),
        **(
            {INTERACTION_KEY: item.interaction.value}
            if item.interaction is not InteractionKind.VIDEO
            else {}
        ),
        ROUTING_KEY: item.routing_value,
    }


def task_body(item: ContactCenterTask, alias_id: str, timestamp_ms: int) -> dict[str, Any]:
    """Return the Create Task v2 request for ``item``, a task of a complete WxCC routing.

    A work item for ``TaskKind.FORM``, a ``customMessaging`` task for ``TaskKind.CONVERSATION``.

    Raises:
        IntegrationError: ``not_ready`` (not retryable) when the routing lacks an attribute, the
            task is one WxCC does not carry (``DESCRIPTOR.capabilities``) or a conversation has
            no first message.
    """
    pair = channel_and_address(item.routing)
    if pair is None or uncarried(
        DESCRIPTOR.capabilities, item.interaction, bool(item.skills), item.task_kind
    ):
        raise IntegrationError("not_ready", retryable=False)
    channel, address = pair
    if item.task_kind is TaskKind.CONVERSATION:
        return conversation_body(item, channel, address, alias_id, timestamp_ms)
    return {
        "origin": {"id": item.reference, "name": item.first_name},
        "destination": {"id": address, "type": "businessAddress"},
        "channelType": "workItem",
        "channel": channel,
        "flowSettings": _flow_settings(item),
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


def conversation_body(
    item: ContactCenterTask, channel: str, address: str, alias_id: str, timestamp_ms: int
) -> dict[str, Any]:
    """Return the Create Task v2 request that opens a conversation for ``item``.

    The booking reference is the participant's identifier and the first name its display name;
    the first message is the text composed at the edge. Nothing else of the booking is sent:
    its fields are the ones of ``ContactCenterTask`` and no more.

    Raises:
        IntegrationError: ``not_ready`` (not retryable) for a blank or too long first message.
    """
    text = item.first_message
    if not text.strip() or len(text) > MESSAGE_TEXT_MAX_LENGTH:
        raise IntegrationError("not_ready", retryable=False)
    return {
        "origin": {"id": item.reference, "name": item.first_name},
        "destination": {"id": address, "type": "businessAddress"},
        "channelType": CONVERSATION_CHANNEL_TYPE,
        "channel": channel,
        "flowSettings": _flow_settings(item),
        "channelParams": {
            "type": "text",
            "message": {"aliasId": alias_id, "text": text, "timestamp": timestamp_ms},
        },
    }


def append_body(message: ParticipantMessage, timestamp_ms: int) -> dict[str, Any]:
    """Return the Task Messages v2 request that appends ``message`` to a conversation.

    ``message.message_id`` is the ``aliasId``: WxCC takes the same one twice as one message.
    """
    return {
        "mediaType": CONVERSATION_CHANNEL_TYPE,
        "channelParams": {
            "type": "text",
            "message": {
                "aliasId": message.message_id,
                "text": message.text,
                "timestamp": timestamp_ms,
            },
        },
    }


def messaging_refusal(error: IntegrationError) -> IntegrationError:
    """Return the domain error for WxCC's refusal of a conversation request.

    A 403 is read as ``messaging_not_configured`` and a 409 as ``conversation_open`` (an open
    conversation exists for the participant identifier). A 403 has more than one cause (the
    feature or channel is not set up, or the service app lacks the right), so the platform's
    status stays on the error as its reason, shown under the event next to the code. Every
    other error is returned as it is. None is retryable, and none is ever answered by creating
    a form task.
    """
    code = {"rejected_403": MESSAGING_NOT_CONFIGURED, "rejected_409": CONVERSATION_OPEN}.get(
        error.code
    )
    if code is None:
        return error
    status = f"HTTP {error.code.removeprefix('rejected_')}"
    return IntegrationError(
        code, retryable=False, reason=f"{status}: {error.reason}" if error.reason else status
    )


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

    async def create_task(self, task: ContactCenterTask) -> str:
        """Create the work item and return its task id."""
        timestamp = int(self._clock.now().timestamp() * 1000)
        body = task_body(task, str(uuid.uuid4()), timestamp)
        try:
            response = await self._client.request(
                "POST", f"{self._base}/v2/tasks", json=body, ok=(200, 201, 202)
            )
        except IntegrationError as error:
            if task.task_kind is TaskKind.CONVERSATION:
                raise messaging_refusal(error) from error
            raise
        task_id = response.json().get("data", {}).get("id")
        if not isinstance(task_id, str):
            raise IntegrationError("unexpected_response", retryable=False)
        return task_id

    async def append_message(self, task: ContactCenterTaskRef, message: ParticipantMessage) -> None:
        """Append the participant's message (``POST /v2/tasks/{taskId}/messages``).

        Accepted (202) is not delivered: WxCC confirms through its events (ADR 0123 step 7b).

        Raises:
            IntegrationError: ``not_ready`` (not retryable) for a task that is not a WxCC
                conversation; ``messaging_not_configured`` and ``conversation_open`` as for
                ``create_task``; otherwise WxCC's refusal or unreachability.
        """
        if (
            task.kind is not ContactCenterKind.WEBEX_CC
            or task.task_kind is not TaskKind.CONVERSATION
        ):
            raise IntegrationError("not_ready", retryable=False)
        timestamp = int(self._clock.now().timestamp() * 1000)
        try:
            await self._client.request(
                "POST",
                self._task_url("v2", task.id, "messages"),
                json=append_body(message, timestamp),
                ok=(200, 202),
            )
        except IntegrationError as error:
            raise messaging_refusal(error) from error

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


def wxcc_tasks(
    config: ServiceAppConfig,
    http: httpx.AsyncClient,
    store: TokenStore,
    clock: Clock,
    base_url: str,
    *,
    security: SecurityLog | None = None,
    request_log: RequestLog | None = None,
    name: str = CREDENTIAL_NAME,
) -> tuple[WebexTokens, WxccTasks]:
    """Return a Service App's tokens and the WxCC dispatcher signed in with them.

    The one wiring of ``ServiceAppConfig`` → ``WebexTokens`` → ``AuthorizedClient`` →
    ``WxccTasks``, for the installation's connection and an organisation's own (``name``).
    """
    tokens = WebexTokens(config, http, store, clock, security, name=name)
    return tokens, WxccTasks(AuthorizedClient(http, tokens, request_log), base_url, clock)
