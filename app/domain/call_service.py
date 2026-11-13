"""Creating a booking's meeting and contact-center task, used by the worker and by direct calls.

Every step is idempotent: a booking that already has its meeting or task is left alone, and
a group without the integration is skipped. The task goes to the dispatcher of the group's
platform (``ContactCenterKind``, ADR 0032); it is ended by the dispatcher of the platform that
holds it. Each success is recorded on the booking's
timeline. How the start time reads for agents is decided at the edge (``start_label``).

Ending a cancelled booking's task (ADR 0021), the cancellation message first:

    end_task ─► mark_cancelled ─► task_marked_cancelled   (refused: integration_error)
             ─► end_task       ─► task_end_requested      (refused: end_not_possible, code)
"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime

from app.domain.authorization import Scope
from app.domain.booking import Booking
from app.domain.calls import (
    ContactCenterTask,
    EventKind,
    TaskState,
    contact_center_task,
    meeting_request,
)
from app.domain.contact_center import TaskKind
from app.domain.contact_center_dispatch import DispatcherChoice
from app.domain.errors import DomainError, ErrorCode
from app.domain.fallback import FallbackService
from app.domain.integration import IntegrationService
from app.domain.interaction import InteractionKind
from app.domain.job_plan import JobType
from app.domain.ports import (
    ContactCenterDispatcher,
    ContactCenterDispatchers,
    IntegrationError,
    OrganisationDispatchers,
    UnitOfWork,
    VideoProvider,
)
from app.domain.scheduling import Group
from app.domain.service_flows import interaction_of, requirement_of
from app.domain.skills import mapped_skill_codes

StartLabel = Callable[[Booking, Group], str]
OpeningText = Callable[[str | None, ContactCenterTask], str]
"""Composes the first message of a conversation task from the organisation's default language
(``None`` when it cannot be read: the edge applies the installation's default) and the task
(ADR 0123 step 7a); the catalogue and the translator are the edge's, so the domain is handed
the function."""
ConversationEnabled = Callable[[], bool]
"""Whether the platform switch ``embedded_call_enabled`` is on now; read at the edge, class C."""
OPENING_MESSAGE = "opening_message"
"""What a refusal names when a conversation task cannot be given its first message."""


class CallService:
    """Creates and removes meetings and contact-center tasks for bookings.

    Args:
        uow: Stores meeting and task ids.
        integration: Records events on the booking's timeline.
        video: Video provider, or ``None`` when Webex is not configured.
        dispatchers: The dispatcher per contact-center platform; empty when none is configured.
        start_label: Formats the start time for agents.
        opening: Composes the first message of a conversation task (ADR 0123 step 7a); without
            it a conversation task cannot be built and is refused as not ready.
        conversation_enabled: Whether the platform switch is on; a routing's conversation is in
            force only while it is (``task_kind_in_force``). Without it, never.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        integration: IntegrationService,
        video: VideoProvider | None,
        dispatchers: ContactCenterDispatchers,
        start_label: StartLabel,
        fallback: FallbackService | None = None,
        organisations: OrganisationDispatchers | None = None,
        opening: OpeningText | None = None,
        conversation_enabled: ConversationEnabled | None = None,
    ) -> None:
        """Store the collaborators; ``fallback`` diverts dispatches to the own queue (ADR 0082).

        ``organisations`` gives each organisation's own dispatcher (ADR 0032 point 9); the
        installation's ``dispatchers`` serve the organisations without one.
        """
        self._uow = uow
        self._integration = integration
        self._video = video
        self._dispatchers = DispatcherChoice(dispatchers, organisations)
        self._start_label = start_label
        self._fallback = fallback
        self._opening = opening
        self._conversation_enabled = conversation_enabled or (lambda: False)

    @property
    def video_available(self) -> bool:
        """Whether meetings can be created."""
        return self._video is not None

    @property
    def dispatch_available(self) -> bool:
        """Whether tasks can be created (needs video for the meeting link)."""
        return self._video is not None and self._dispatchers.configured

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
        """Create the booking's task if the group dispatches and the booking has none yet.

        The task goes to the dispatcher of the group's platform; a group whose platform has
        no dispatcher configured is skipped like a group without a contact center.

        The interaction kind is read before the meeting is waited for: only a video task waits
        for one (ADR 0121 step 6b).

        Raises:
            IntegrationError: ``meeting_missing`` (retryable) while a video task's meeting is
                not there.
        """
        routing = group.contact_center
        if routing is None or not group.dispatch_enabled or booking.contact_center_task:
            return booking
        dispatcher = await self._dispatchers.for_organisation(group.organisation_id, routing.kind)
        if dispatcher is None:
            return booking
        if await self.diverts(booking, group):
            return booking
        built = await self.task_for(booking, group)
        task_id = await dispatcher.create_task(built)
        booking_id, task = _id(booking), built.ref(task_id)
        async with self._uow.locked() as tx:
            await tx.save_task(booking_id, task)
        await self._integration.record(booking_id, EventKind.TASK_CREATED)
        return _with(booking, contact_center_task=task)

    async def task_for(self, booking: Booking, group: Group) -> ContactCenterTask:
        """Return the neutral task of ``booking``: its flow's interaction kind and skill codes.

        Raises:
            IntegrationError: ``meeting_missing`` (retryable) while a video task's meeting is
                not there.
            DomainError: ``integration_not_ready`` (``skill_codes``, ``interaction_kind``,
                ``task_kind``, ``opening_message``, ...).
        """
        interaction, skills = await self._flow_terms(booking, group)
        if interaction is InteractionKind.VIDEO and booking.meeting is None:
            raise IntegrationError("meeting_missing", retryable=True)
        task = contact_center_task(
            booking,
            group,
            start_label=self._start_label(booking, group),
            skills=skills,
            interaction=interaction,
            conversation_enabled=self._conversation_enabled(),
        )
        if task.task_kind is not TaskKind.CONVERSATION:
            return task
        if self._opening is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY, OPENING_MESSAGE)
        async with self._uow.read() as tx:
            organisation = await tx.get_organisation_by_id(group.organisation_id)
        locale = organisation.default_locale if organisation else None
        return replace(task, first_message=self._opening(locale, task))

    async def dispatcher_of(self, group: Group) -> ContactCenterDispatcher | None:
        """Return the dispatcher of the group's contact-center platform, ``None`` without one."""
        routing = group.contact_center
        if routing is None or not routing.complete:
            return None
        return await self._dispatchers.for_organisation(group.organisation_id, routing.kind)

    async def _flow_terms(
        self, booking: Booking, group: Group
    ) -> tuple[InteractionKind, tuple[str, ...]]:
        """Return the interaction kind of ``booking`` and the codes of the skills it requires.

        Both come from the flow revision recorded on the booking, read once: its interaction
        kind (ADR 0121 step 6) and the contact-center codes of its required skills (step 4b). A
        booking without a flow is a video booking that requires nothing, and a flow without
        skills (every legacy flow) reads no skill.

        Raises:
            DomainError: ``integration_not_ready`` (``skill_codes``) for a required skill
                without a code: an unmapped requirement is never dropped; (``interaction_kind``)
                for a recorded flow whose revision is gone or names no interaction kind.
        """
        if booking.flow is None:
            return interaction_of(None, recorded=False), ()
        async with self._uow.read() as tx:
            revision = await tx.flow_revision(booking.flow.flow_id, booking.flow.revision)
            interaction = interaction_of(revision, recorded=True)
            subject = booking.routing.subject if booking.routing else None
            required = requirement_of(revision, subject)
            if not required:
                return interaction, ()
            summary = await tx.get_group_summary(group.slug)
            scope = Scope(organisations=frozenset({summary.organisation_slug} if summary else ()))
            skills = {i: s for i in required if (s := await tx.get_skill(scope, i)) is not None}
        return interaction, mapped_skill_codes(required, skills)

    async def diverts(self, booking: Booking, group: Group) -> bool:
        """Put a booking due for dispatch in the own queue while its group is in fallback."""
        return self._fallback is not None and await self._fallback.diverts(booking, group)

    async def dispatch_failed(self, booking_id: int) -> None:
        """Hand a dispatch that failed for good to the fallback (ADR 0082 point 11)."""
        if self._fallback is not None:
            await self._fallback.dispatch_failed(booking_id)

    async def create_now(self, booking: Booking, group: Group, now: datetime) -> None:
        """Create the meeting and task of a direct call in the request, not the worker.

        Each step first takes the booking's pending job the way the worker claims it, so the
        worker leaves it alone and only one side makes the external resource. A step the
        worker holds is skipped; a failed step puts its job back for the worker to retry.

        Raises:
            IntegrationError: From the step that failed, after its job was put back.
        """
        booking_id = _id(booking)
        booking = await self._claimed(
            booking_id, JobType.CREATE_VIDEO_ROOM, now, lambda: self.ensure_meeting(booking, group)
        )
        await self._claimed(
            booking_id,
            JobType.DISPATCH_CONTACT_CENTER_TASK,
            now,
            lambda: self.ensure_task(booking, group),
        )

    async def _claimed(
        self,
        booking_id: int,
        job_type: JobType,
        now: datetime,
        step: Callable[[], Awaitable[Booking]],
    ) -> Booking:
        """Run ``step`` while holding the booking's ``job_type`` job; ``step``'s booking back."""
        async with self._uow.locked() as tx:
            if not await tx.claim_booking_job(booking_id, job_type, now):
                return await self._reload(booking_id)
        done = False
        try:
            result = await step()
            done = True
            return result
        finally:
            async with self._uow.locked() as tx:
                await tx.finish_booking_job(booking_id, job_type, done=done)

    async def _reload(self, booking_id: int) -> Booking:
        """Return the stored booking when the worker holds the step, so the next sees its result."""
        async with self._uow.read() as tx:
            stored = await tx.get_booking(booking_id)
        if stored is None:
            raise IntegrationError("booking_missing", retryable=False)
        return stored

    @property
    def end_available(self) -> bool:
        """Whether tasks can be ended (needs only a contact center)."""
        return self._dispatchers.configured

    async def end_task(self, booking: Booking, group: Group) -> None:
        """Mark a cancelled booking's task cancelled, then try to end it.

        Nothing happens without a task, without a dispatcher for the platform that holds it, or
        once the platform reported it ended or failed. A retry after a successful mark does not
        mark again. A conversation task (ADR 0123 step 7a) is never marked: the mark is the work
        item's cancellation form, which is not a message of a conversation, so it is ended at
        once and a failed mark can never leave it open with the meeting link in it.

        Raises:
            IntegrationError: Retryable failures, so the job tries again.
        """
        task = booking.contact_center_task
        if task is None:
            return
        dispatcher = await self._dispatchers.for_organisation(group.organisation_id, task.kind)
        if dispatcher is None:
            return
        task_id, booking_id = task.id, _id(booking)
        status = await self._integration.status(booking_id)
        if status.task in (TaskState.ENDED, TaskState.FAILED):
            return
        if task.task_kind is TaskKind.FORM and status.task is not TaskState.CANCELLED:
            try:
                await dispatcher.mark_cancelled(task_id, booking.reference)
            except IntegrationError as error:
                if error.retryable:
                    raise
                await self._integration.record(
                    booking_id, EventKind.INTEGRATION_ERROR, detail=error.code
                )
            else:
                await self._integration.record(booking_id, EventKind.TASK_MARKED_CANCELLED)
        try:
            await dispatcher.end_task(task_id)
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

    async def delete_trial_call(self, booking: Booking, group: Group) -> None:
        """Delete the booking's trial meeting, if it has one, and forget it (ADR 0071).

        Runs after the trial call closed, and at once when the booking is cancelled or moved.
        """
        if self._video is None or booking.trial is None or group.video_host_email is None:
            return
        booking_id = _id(booking)
        await self._video.delete_meeting(booking.trial.meeting.id, group.video_host_email)
        async with self._uow.locked() as tx:
            await tx.clear_trial_call(booking_id)
        await self._integration.record(booking_id, EventKind.TRIAL_DELETED)

    async def record_failure(
        self, booking_id: int, code: str, kind: EventKind, message: str = ""
    ) -> None:
        """Put a final failure of kind ``kind`` with reason ``code`` on the booking's timeline.

        ``message`` is the provider's own reason (ADR 0065), shown under the event.
        """
        await self._integration.record(booking_id, kind, detail=code, message=message)


def _with(booking: Booking, **changes: object) -> Booking:
    """Return ``booking`` with ``changes`` applied."""
    return replace(booking, **changes)  # type: ignore[arg-type]


def _id(booking: Booking) -> int:
    """Return the id of a stored booking."""
    if booking.id is None:  # pragma: no cover - stored bookings always have an id
        raise ValueError("booking has no id")
    return booking.id
