"""Fakes of the integration ports: they record what they were asked, answer or fail on demand.

The Webex and WxCC adapters (video, dispatch, probes, checks) and the request log those
adapters report every call to (ADR 0068).
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from app.domain.alert_model import AlertChannelKind, AlertTarget, RenderedAlert
from app.domain.booking import MeetingRef
from app.domain.calls import (
    CallState,
    ContactCenterTask,
    EventKind,
    MeetingRequest,
    MeetingStatus,
    ParticipantMessage,
    TaskSnapshot,
    TaskState,
)
from app.domain.checks import Finding
from app.domain.contact_center import (
    ConnectionChange,
    ContactCenterKind,
    ContactCenterTaskRef,
    OrganisationConnection,
    Registration,
    TaskKind,
)
from app.domain.guest_access import GuestAccess
from app.domain.networks import NetworkList
from app.domain.ports import (
    ContactCenterDispatcher,
    IntegrationError,
    OwnConnection,
    RequestRecord,
)
from app.domain.scheduling import Group

GUEST_EXPIRES = datetime(2026, 1, 1, 18, tzinfo=UTC)
"""When the fake's guest tokens expire."""


class FakeVideo:
    def __init__(self) -> None:
        self.created: list[MeetingRequest] = []
        self.deleted: list[tuple[str, str]] = []
        self.error: IntegrationError | None = None
        self.polled: list[tuple[str, str]] = []
        self.statuses: dict[str, MeetingStatus] = {}
        self.status_error: IntegrationError | None = None
        self.guests: list[tuple[str, str]] = []
        self.guest_error: IntegrationError | None = None

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

    async def guest_access(self, subject: str, display_name: str) -> GuestAccess:
        if self.guest_error:
            raise self.guest_error
        self.guests.append((subject, display_name))
        return GuestAccess(f"guest-token-{len(self.guests)}", GUEST_EXPIRES)


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
        self.kinds: list[TaskKind] = []
        """The kind of every task created, in order (ADR 0123 step 7a)."""
        self.appended: list[tuple[str, ParticipantMessage]] = []
        """The messages appended to conversation tasks, once per message identifier."""
        self.append_error: IntegrationError | None = None

    async def create_task(self, task: ContactCenterTask) -> str:
        if self.error:
            raise self.error
        self.items.append(task)
        self.kinds.append(task.task_kind)
        return f"task-{len(self.items)}"

    async def append_message(self, task: ContactCenterTaskRef, message: ParticipantMessage) -> None:
        if self.append_error:
            raise self.append_error
        if task.task_kind is not TaskKind.CONVERSATION:
            raise IntegrationError("not_ready", retryable=False)
        if all(m.message_id != message.message_id for _, m in self.appended):
            self.appended.append((task.id, message))  # the platform takes one id as one message

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
        self.secretless: set[int] = set()
        """Organisations whose record has no webhook secret."""
        self.unreadable: set[int] = set()
        """Organisations whose stored list cannot be read (until a save replaces it)."""

    async def get(self, organisation_id: int) -> OrganisationConnection | None:
        shown = self.connections.get(organisation_id)
        if shown is not None and organisation_id in self.unreadable:
            return replace(shown, allowed_networks=NetworkList(unreadable=True))
        return shown

    async def save(self, organisation_id: int, change: ConnectionChange, now: datetime) -> None:
        old = self.connections.get(organisation_id)
        if change.allowed_networks is not None:
            self.unreadable.discard(organisation_id)
        self.connections[organisation_id] = OrganisationConnection(
            change.kind,
            change.api_base_url,
            change.client_id,
            has_client_secret=bool(change.client_secret) or bool(old and old.has_client_secret),
            has_refresh_token=bool(change.refresh_token) or bool(old and old.has_refresh_token),
            webhook_secret_at=old.webhook_secret_at if old else now,
            allowed_networks=(
                NetworkList(change.allowed_networks)
                if change.allowed_networks is not None
                else (old.allowed_networks if old else NetworkList())
            ),
            inbound_enabled=old.inbound_enabled if old else True,
        )

    async def set_inbound_enabled(self, organisation_id: int, enabled: bool, now: datetime) -> None:
        old = self.connections.get(organisation_id)
        if old is not None:
            self.connections[organisation_id] = replace(old, inbound_enabled=enabled)

    async def rotate_webhook_secret(self, organisation_id: int, now: datetime) -> None:
        old = self.connections.get(organisation_id)
        if old is not None:
            self.connections[organisation_id] = replace(old, webhook_secret_at=now)
            self.rotations += 1

    async def delete(self, organisation_id: int) -> None:
        self.connections.pop(organisation_id, None)

    async def webhook_secret(self, organisation_id: int, kind: ContactCenterKind) -> str | None:
        shown = self.connections.get(organisation_id)
        if organisation_id in self.secretless or shown is None or shown.kind is not kind:
            return None
        return f"secret-of-{organisation_id}"


class FakeOrganisationDispatchers:
    """``OrganisationConnections`` in memory: per organisation id its dispatcher, or ``None``;
    registering answers ``outcome`` and is remembered."""

    def __init__(
        self,
        own: dict[int, ContactCenterDispatcher | None] | None = None,
        outcome: Registration = Registration.REGISTERED,
    ) -> None:
        self.connections = own or {}
        self.outcome = outcome
        self.registered: list[tuple[int, str]] = []

    async def register(self, organisation_id: int, organisation: str) -> Registration:
        self.registered.append((organisation_id, organisation))
        return self.outcome

    async def own(self, organisation_id: int, kind: ContactCenterKind) -> OwnConnection | None:
        if organisation_id not in self.connections:
            return None
        return OwnConnection(self.connections[organisation_id])
