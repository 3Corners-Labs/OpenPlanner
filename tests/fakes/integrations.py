"""Fake Webex and WxCC adapters: record what they were asked, answer or fail on demand."""

from collections.abc import Sequence

from app.domain.booking import MeetingRef
from app.domain.calls import MeetingRequest, WorkItem
from app.domain.checks import Finding
from app.domain.ports import IntegrationError
from app.domain.scheduling import Group


class FakeVideo:
    def __init__(self) -> None:
        self.created: list[MeetingRequest] = []
        self.deleted: list[tuple[str, str]] = []
        self.error: IntegrationError | None = None

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


class FakeDispatcher:
    def __init__(self) -> None:
        self.items: list[WorkItem] = []
        self.error: IntegrationError | None = None
        self.ended: list[str] = []
        self.end_error: IntegrationError | None = None
        self.marked: list[tuple[str, str]] = []
        self.mark_error: IntegrationError | None = None

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
