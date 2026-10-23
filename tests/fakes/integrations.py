"""Fake Webex and WxCC adapters: record what they were asked, answer or fail on demand."""

from app.domain.booking import MeetingRef
from app.domain.calls import MeetingRequest, WorkItem
from app.domain.ports import IntegrationError


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

    async def create_task(self, item: WorkItem) -> str:
        if self.error:
            raise self.error
        self.items.append(item)
        return f"task-{len(self.items)}"
