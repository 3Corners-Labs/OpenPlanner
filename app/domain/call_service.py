"""Creating a booking's meeting and work item, used by the worker and by direct calls.

Every step is idempotent: a booking that already has its meeting or task is left alone, and
a group without the integration is skipped. Each success is recorded on the booking's
timeline. How the start time reads for agents is decided at the edge (``start_label``).

Ending a cancelled booking's work item (ADR 0021), v2 first:

    end_task ─► Task Messages v2 ─► task_marked_cancelled   (refused: integration_error)
             ─► End Task v1     ─► task_end_requested      (refused: end_not_possible, code)
"""

from collections.abc import Callable
from dataclasses import replace

from app.domain.booking import Booking
from app.domain.calls import EventKind, TaskState, meeting_request, work_item
from app.domain.integration import IntegrationService
from app.domain.ports import ContactCenterDispatcher, IntegrationError, UnitOfWork, VideoProvider
from app.domain.scheduling import Group

StartLabel = Callable[[Booking, Group], str]


class CallService:
    """Creates and removes meetings and work items for bookings.

    Args:
        uow: Stores meeting and task ids.
        integration: Records events on the booking's timeline.
        video: Video provider, or ``None`` when Webex is not configured.
        dispatcher: WxCC dispatcher, or ``None`` when WxCC is not configured.
        start_label: Formats the start time for agents.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        integration: IntegrationService,
        video: VideoProvider | None,
        dispatcher: ContactCenterDispatcher | None,
        start_label: StartLabel,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._integration = integration
        self._video = video
        self._dispatcher = dispatcher
        self._start_label = start_label

    @property
    def video_available(self) -> bool:
        """Whether meetings can be created."""
        return self._video is not None

    @property
    def dispatch_available(self) -> bool:
        """Whether work items can be created (needs video for the meeting link)."""
        return self._video is not None and self._dispatcher is not None

    async def ensure_meeting(self, booking: Booking, group: Group) -> Booking:
        """Create the booking's meeting if the group has video and it has none yet."""
        if self._video is None or not group.video_enabled or booking.meeting is not None:
            return booking
        meeting = await self._video.create_meeting(meeting_request(booking, group))
        booking_id = _id(booking)
        async with self._uow.locked() as tx:
            await tx.save_meeting(booking_id, meeting)
        await self._integration.record(booking_id, EventKind.MEETING_CREATED)
        return _with(booking, meeting=meeting)

    async def ensure_task(self, booking: Booking, group: Group) -> Booking:
        """Create the booking's work item if WxCC is on for the group and it has none yet.

        Raises:
            IntegrationError: ``meeting_missing`` (retryable) while the meeting is not there.
        """
        if self._dispatcher is None or not group.dispatch_enabled or booking.wxcc_task_id:
            return booking
        if booking.meeting is None:
            raise IntegrationError("meeting_missing", retryable=True)
        task_id = await self._dispatcher.create_task(
            work_item(booking, group, start_label=self._start_label(booking, group))
        )
        booking_id = _id(booking)
        async with self._uow.locked() as tx:
            await tx.save_task_id(booking_id, task_id)
        await self._integration.record(booking_id, EventKind.TASK_CREATED)
        return _with(booking, wxcc_task_id=task_id)

    @property
    def end_available(self) -> bool:
        """Whether work items can be ended (needs only WxCC)."""
        return self._dispatcher is not None

    async def end_task(self, booking: Booking) -> None:
        """Mark a cancelled booking's work item cancelled (v2), then try to end it (v1).

        Nothing happens without a task, or once WxCC reported it ended or failed. A retry
        after a successful mark does not mark again.

        Raises:
            IntegrationError: Retryable failures, so the job tries again.
        """
        if self._dispatcher is None or not booking.wxcc_task_id:
            return
        task_id, booking_id = booking.wxcc_task_id, _id(booking)
        status = await self._integration.status(booking_id)
        if status.task in (TaskState.ENDED, TaskState.FAILED):
            return
        if status.task is not TaskState.CANCELLED:
            try:
                await self._dispatcher.mark_cancelled(task_id, booking.reference)
            except IntegrationError as error:
                if error.retryable:
                    raise
                await self._integration.record(
                    booking_id, EventKind.INTEGRATION_ERROR, detail=error.code
                )
            else:
                await self._integration.record(booking_id, EventKind.TASK_MARKED_CANCELLED)
        try:
            await self._dispatcher.end_task(task_id)
        except IntegrationError as error:
            if error.retryable:
                raise
            await self._integration.record(
                booking_id, EventKind.END_NOT_POSSIBLE, detail=error.code
            )
            return
        await self._integration.record(booking_id, EventKind.TASK_END_REQUESTED)

    async def delete_meeting(self, booking: Booking, group: Group) -> None:
        """Delete the booking's meeting, if it has one."""
        if self._video is None or booking.meeting is None or group.video_host_email is None:
            return
        await self._video.delete_meeting(booking.meeting.id, group.video_host_email)
        await self._integration.record(_id(booking), EventKind.MEETING_DELETED)

    async def record_failure(self, booking_id: int, code: str, kind: EventKind) -> None:
        """Put a final failure of kind ``kind`` with reason ``code`` on the booking's timeline."""
        await self._integration.record(booking_id, kind, detail=code)


def _with(booking: Booking, **changes: object) -> Booking:
    """Return ``booking`` with ``changes`` applied."""
    return replace(booking, **changes)  # type: ignore[arg-type]


def _id(booking: Booking) -> int:
    """Return the id of a stored booking."""
    if booking.id is None:  # pragma: no cover - stored bookings always have an id
        raise ValueError("booking has no id")
    return booking.id
