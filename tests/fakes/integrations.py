"""Fakes of the integration ports: they record what they were asked, answer or fail on demand.

The Webex and WxCC adapters (video, dispatch, probes, checks) and the request log those
adapters report every call to (ADR 0068).
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from app.domain.alert_model import AlertChannelKind, AlertTarget, RenderedAlert
from app.domain.booking import MeetingRef
from app.domain.calls import (
    CallState,
    ContactCenterTask,
    EventKind,
    MeetingRequest,
    MeetingStatus,
    TaskSnapshot,
    TaskState,
)
from app.domain.checks import Finding
from app.domain.contact_center import ConnectionChange, ContactCenterKind, OrganisationConnection
from app.domain.ports import (
    ContactCenterDispatcher,
    IntegrationError,
    OwnConnection,
    RequestRecord,
)
from app.domain.scheduling import Group


class FakeVideo:
    def __init__(self) -> None:
        self.created: list[MeetingRequest] = []
        self.deleted: list[tuple[str, str]] = []
        self.error: IntegrationError | None = None
        self.polled: list[tuple[str, str]] = []
        self.statuses: dict[str, MeetingStatus] = {}
        self.status_error: IntegrationError | None = None

    async def create_meeting(self, request: MeetingRequest) -> MeetingRef:
        if self.error:
            raise self.error
        self.created.append(request)
        return MeetingRef(
            f"meeting-{len(self.created)}", f"https://site.webex.com/m/{len(self.created)}"
        )

    async def delete_meeting(self, meeting_id: str, host_email: str) -> None:
        if self.error:
            raise self.error
        self.deleted.append((meeting_id, host_email))

    async def meeting_status(self, meeting_id: str, host_email: str) -> MeetingStatus:
        self.polled.append((meeting_id, host_email))
        if self.status_error:
            raise self.status_error
        return self.statuses.get(meeting_id, MeetingStatus(CallState.SCHEDULED))


def webex_cc(dispatcher: "FakeDispatcher") -> dict[ContactCenterKind, ContactCenterDispatcher]:
    """Return ``dispatcher`` as the application's dispatcher of Webex Contact Center."""
    return {ContactCenterKind.WEBEX_CC: dispatcher}


class FakeDispatcher:
    def __init__(self) -> None:
        self.items: list[ContactCenterTask] = []
        self.error: IntegrationError | None = None
        self.ended: list[str] = []
        self.end_error: IntegrationError | None = None
        self.marked: list[tuple[str, str]] = []
        self.mark_error: IntegrationError | None = None
        self.read: list[str] = []
        self.snapshots: dict[str, TaskSnapshot] = {}
        self.read_error: IntegrationError | None = None

    async def create_task(self, task: ContactCenterTask) -> str:
        if self.error:
            raise self.error
        self.items.append(task)
        return f"task-{len(self.items)}"

    async def end_task(self, task_id: str) -> None:
        if self.end_error:
            raise self.end_error
        self.ended.append(task_id)

    async def mark_cancelled(self, task_id: str, reference: str) -> None:
        if self.mark_error:
            raise self.mark_error
        self.marked.append((task_id, reference))

    async def read_task(self, task_id: str) -> TaskSnapshot:
        self.read.append(task_id)
        if self.read_error:
            raise self.read_error
        return self.snapshots.get(task_id, TaskSnapshot(TaskState.NONE))


class FakeProbes:
    """``ProbeRegistry`` in memory: events arrive as scripted, one per look (ADR 0082)."""

    def __init__(self, arriving: list[EventKind] | None = None) -> None:
        self.opened: list[str] = []
        self.noted: list[tuple[str, EventKind]] = []
        self.arriving = list(arriving or [])
        """Events handed out one per ``events`` call, as if the webhook brought them."""
        self._task_id: str | None = None
        self._events: list[EventKind] = []

    async def open(self, task_id: str, at: datetime) -> None:
        self.opened.append(task_id)
        self._task_id, self._events = task_id, []

    async def note(self, task_id: str, kind: EventKind, at: datetime) -> bool:
        if task_id != self._task_id:
            return False
        self.noted.append((task_id, kind))
        self._events.append(kind)
        return True

    async def events(self, task_id: str) -> tuple[EventKind, ...]:
        if task_id != self._task_id:
            return ()
        if self.arriving:
            self._events.append(self.arriving.pop(0))
        return tuple(self._events)


class FakeChecker:
    """``IntegrationChecker`` that returns fixed findings and remembers what it was asked."""

    def __init__(self, findings: Sequence[Finding], error: Exception | None = None) -> None:
        self.findings = list(findings)
        self.error = error
        self.calls: list[tuple[str, list[str]]] = []

    async def check_group(self, group: Group, peers: Sequence[Group]) -> list[Finding]:
        self.calls.append((group.slug, sorted(p.slug for p in peers)))
        if self.error is not None:
            raise self.error
        return self.findings


class FakeRequestLog:
    """``RequestLog`` that keeps every record it was given; with ``error`` it raises (ADR 0068)."""

    def __init__(self, error: Exception | None = None) -> None:
        self.records: list[RequestRecord] = []
        self.error = error

    async def record(self, record: RequestRecord) -> None:
        if self.error is not None:
            raise self.error
        self.records.append(record)


class FakeAlertSender:
    """``AlertSender`` that keeps what it was asked to send, or fails with ``error``."""

    def __init__(
        self, sent: list[tuple[str, RenderedAlert]], error: IntegrationError | None
    ) -> None:
        self.sent = sent
        self.error = error

    async def send(self, address: str, alert: RenderedAlert) -> None:
        if self.error:
            raise self.error
        self.sent.append((address, alert))


class FakeAlertSenders:
    """``AlertSenders`` in memory: every target has a sender unless its kind is switched off."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, RenderedAlert]] = []
        self.error: IntegrationError | None = None
        self.missing: set[AlertChannelKind] = set()

    async def sender(self, target: AlertTarget) -> FakeAlertSender | None:
        if target.kind in self.missing:
            return None
        return FakeAlertSender(self.sent, self.error)


class FakeBotTokens:
    """``BotTokenStore`` in memory."""

    def __init__(self) -> None:
        self.tokens: dict[int | None, str] = {}

    async def has_token(self, organisation_id: int | None) -> bool:
        return organisation_id in self.tokens

    async def save(self, organisation_id: int | None, token: str | None, now: datetime) -> None:
        if token:
            self.tokens[organisation_id] = token
        else:
            self.tokens.pop(organisation_id, None)


class FakeSecrets:
    """The encrypted credential store (``CredentialStore``) in memory, for the bot tokens."""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    async def get(self, name: str) -> dict[str, object] | None:
        return self.values.get(name)

    async def put(self, name: str, value: dict[str, object], now: datetime) -> None:
        self.values[name] = value

    async def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FakeContactCenterConnections:
    """``ContactCenterConnections`` in memory: the views and a counter of rotations."""

    def __init__(self) -> None:
        self.connections: dict[int, OrganisationConnection] = {}
        self.rotations = 0

    async def get(self, organisation_id: int) -> OrganisationConnection | None:
        return self.connections.get(organisation_id)

    async def save(self, organisation_id: int, change: ConnectionChange, now: datetime) -> None:
        old = self.connections.get(organisation_id)
        self.connections[organisation_id] = OrganisationConnection(
            change.kind,
            change.api_base_url,
            change.client_id,
            has_client_secret=bool(change.client_secret) or bool(old and old.has_client_secret),
            has_refresh_token=bool(change.refresh_token) or bool(old and old.has_refresh_token),
            webhook_secret_at=old.webhook_secret_at if old else now,
        )

    async def rotate_webhook_secret(self, organisation_id: int, now: datetime) -> None:
        old = self.connections.get(organisation_id)
        if old is not None:
            self.connections[organisation_id] = replace(old, webhook_secret_at=now)
            self.rotations += 1

    async def delete(self, organisation_id: int) -> None:
        self.connections.pop(organisation_id, None)


class FakeOrganisationDispatchers:
    """``OrganisationDispatchers`` in memory: per organisation id its dispatcher, or ``None``."""

    def __init__(self, own: dict[int, ContactCenterDispatcher | None] | None = None) -> None:
        self.connections = own or {}

    async def own(self, organisation_id: int, kind: ContactCenterKind) -> OwnConnection | None:
        if organisation_id not in self.connections:
            return None
        return OwnConnection(self.connections[organisation_id])
