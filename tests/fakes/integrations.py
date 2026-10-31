"""Fake Webex and WxCC adapters: record what they were asked, answer or fail on demand."""

from collections.abc import Sequence
from datetime import datetime

from app.domain.booking import MeetingRef
from app.domain.calls import (
    CallState,
    EventKind,
    MeetingRequest,
    MeetingStatus,
    TaskSnapshot,
    TaskState,
    WorkItem,
)
from app.domain.checks import Finding
from app.domain.ports import IntegrationError
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


class FakeDispatcher:
    def __init__(self) -> None:
        self.items: list[WorkItem] = []
        self.error: IntegrationError | None = None
        self.ended: list[str] = []
        self.end_error: IntegrationError | None = None
        self.marked: list[tuple[str, str]] = []
        self.mark_error: IntegrationError | None = None
        self.read: list[str] = []
        self.snapshots: dict[str, TaskSnapshot] = {}
        self.read_error: IntegrationError | None = None

    async def create_task(self, item: WorkItem) -> str:
        if self.error:
            raise self.error
        self.items.append(item)
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
