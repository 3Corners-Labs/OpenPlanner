"""Opening and joining a trial call on Webex (ADR 0071); the rules are in ``trial_call``.

    view   ─► state of the booking's trial call (read only)
    open   ─► AVAILABLE: schedule the meeting at Webex ─► locked tx: store, count, plan the
              delete job ─► record ``trial_created``; ACTIVE: reuse; anything else: unchanged
    join   ─► the link of an ACTIVE trial call, recorded as ``trial_joined``
    enter  ─► for a service that uses the call page (ADR 0123 point 9): the open trial call is
              entered in our page, recorded as ``trial_joined`` too

The meeting is scheduled outside the write lock (a slow Webex must not hold the database). If
two requests race, the second finds the first one stored and deletes its own meeting again. A
failure at Webex is recorded on the booking's timeline with Webex's reason and shown to the
participant as "not possible now"; it does not count as a trial call.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.domain.booking import Booking, MeetingRef, TrialCall
from app.domain.call_client import redirect_client, uses_call_page
from app.domain.calls import EventKind, MeetingRequest
from app.domain.calls import join_link as client_link
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType, PlannedJob
from app.domain.participant_links import PublicRef
from app.domain.ports import Clock, IntegrationError, Transaction, UnitOfWork, VideoProvider
from app.domain.scheduling import Group
from app.domain.tokens import hash_token, token_matches
from app.domain.trial_call import CLEANUP_DELAY, TrialState, trial_request, trial_state


@dataclass(frozen=True)
class TrialView:
    """What the trial call page shows: the group, the state and, while open, when it closes.

    ``failed`` is set when this very request tried to open one and Webex refused or could not
    be reached. ``calls_left`` counts the trial calls the booking may still open. ``call_page``
    says that the booking's service uses the call page of our own (ADR 0123 point 9): the page
    then speaks of a call in this browser and its button leads to the call page.
    """

    group: Group
    booking: Booking
    state: TrialState
    expires_at: datetime | None = None
    calls_left: int = 0
    failed: bool = False
    call_page: bool = False


class TrialCallService:
    """Opens trial calls for participants.

    Args:
        uow: Reads and writes bookings and jobs.
        video: Video provider, or ``None`` when Webex is not configured.
        integration: Records events on the booking's timeline.
        clock: Source of the current time.
        call_page_enabled: Whether the platform switch ``embedded_call_enabled`` is on now; with
            a service whose client is ``embedded`` it decides ``uses_call_page`` (ADR 0123 step
            6b). Without it, never.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        video: VideoProvider | None,
        integration: IntegrationService,
        clock: Clock,
        call_page_enabled: Callable[[], bool] | None = None,
    ) -> None:
        """Store the collaborators."""
        self._call_page_enabled = call_page_enabled or (lambda: False)
        self._uow = uow
        self._video = video
        self._integration = integration
        self._clock = clock

    async def view(self, management_token: str | PublicRef) -> TrialView:
        """Return the state of the booking's trial call.

        Raises:
            DomainError: ``booking_not_found``.
        """
        async with self._uow.read() as tx:
            booking, group = await self._load(tx, management_token)
        return self._view(booking, group)

    async def open(self, management_token: str | PublicRef) -> TrialView:
        """Open a trial call when the booking may have one, else return its state unchanged.

        Raises:
            DomainError: ``booking_not_found``.
        """
        async with self._uow.read() as tx:
            booking, group = await self._load(tx, management_token)
        view = self._view(booking, group)
        if view.state is not TrialState.AVAILABLE:
            return view
        now = self._clock.now()
        request = trial_request(group, now, call_page=self._call_page(group))
        try:
            meeting = await self._schedule(request)
        except IntegrationError as error:
            await self._integration.record(
                _id(booking), EventKind.TRIAL_FAILED, detail=error.code, message=error.reason
            )
            return TrialView(
                group,
                booking,
                TrialState.AVAILABLE,
                calls_left=view.calls_left,
                failed=True,
                call_page=view.call_page,
            )
        trial = TrialCall(meeting, request.end)
        replaced: TrialCall | None = None
        async with self._uow.locked() as tx:
            current, _ = await self._load(tx, management_token)
            if trial_state(current, group, now) is not TrialState.AVAILABLE:
                stored = False
            else:
                replaced = current.trial
                await tx.save_trial_call(_id(current), trial)
                # Pinned to this trial: a cleanup that finds another trial leaves it alone.
                await tx.add_jobs(
                    _id(current),
                    [
                        PlannedJob(
                            JobType.DELETE_TRIAL_CALL,
                            trial.expires_at + CLEANUP_DELAY,
                            trial.expires_at,
                        )
                    ],
                )
                stored = True
        if not stored:
            await self._video_delete(meeting.id, group)
        else:
            if replaced is not None:  # its cleanup job no longer finds it: remove it here
                await self._video_delete(replaced.meeting.id, group)
            await self._integration.record(_id(booking), EventKind.TRIAL_CREATED)
        async with self._uow.read() as tx:
            booking, group = await self._load(tx, management_token)
        return self._view(booking, group)

    async def join_link(self, management_token: str | PublicRef) -> str | None:
        """Return the link into the open trial call and record that it was followed.

        ``None`` when there is no open trial call (the caller sends the participant back).

        Raises:
            DomainError: ``booking_not_found``.
        """
        async with self._uow.read() as tx:
            booking, group = await self._load(tx, management_token)
        # Same builder as the appointment's link (ADR 0075): https only, the redirect's shape for
        # the group's client. The event records the shape the participant really got.
        shape = redirect_client(group)
        link = client_link(booking.trial.meeting.link if booking.trial else None, shape)
        if trial_state(booking, group, self._clock.now()) is not TrialState.ACTIVE or not link:
            return None
        await self._integration.record(_id(booking), EventKind.TRIAL_JOINED, detail=shape.value)
        return link

    async def enter_page(self, management_token: str | PublicRef) -> TrialView | None:
        """Record that the participant opened the call page on the open trial call.

        For a service that uses the call page only (ADR 0123 point 9). ``None`` when there is no
        open trial call or the service does not use the page: the caller sends the participant
        back to the trial page.

        Raises:
            DomainError: ``booking_not_found``.
        """
        async with self._uow.read() as tx:
            booking, group = await self._load(tx, management_token)
        state = trial_state(booking, group, self._clock.now())
        if not self._call_page(group) or state is not TrialState.ACTIVE:
            return None
        await self._integration.record(_id(booking), EventKind.TRIAL_JOINED, detail="embedded")
        return self._view(booking, group)

    def _call_page(self, group: Group) -> bool:
        """Return whether a booking of ``group`` has the call page (``uses_call_page``)."""
        return uses_call_page(group, platform_enabled=self._call_page_enabled())

    def _view(self, booking: Booking, group: Group) -> TrialView:
        """Return the page's view of ``booking`` now."""
        state = trial_state(booking, group, self._clock.now())
        # Only opening a new trial call needs Webex; one that is already open keeps its way in.
        if self._video is None and state is TrialState.AVAILABLE:
            state = TrialState.OFF
        expires = booking.trial.expires_at if booking.trial and state is TrialState.ACTIVE else None
        left = max(0, group.trial_calls_per_booking - booking.trial_count)
        return TrialView(group, booking, state, expires, left, call_page=self._call_page(group))

    async def _schedule(self, request: MeetingRequest) -> MeetingRef:
        """Schedule the trial meeting at Webex.

        Raises:
            DomainError: ``integration_not_ready`` without a video provider.
            IntegrationError: Webex refused or could not be reached.
        """
        if self._video is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "video")
        return await self._video.create_meeting(request)

    async def _video_delete(self, meeting_id: str, group: Group) -> None:
        """Delete a meeting that lost a race; a failure leaves an empty room that closes itself."""
        if self._video is not None and group.video_host_email is not None:
            try:
                await self._video.delete_meeting(meeting_id, group.video_host_email)
            except IntegrationError:
                return

    async def _load(
        self, tx: Transaction, management_token: str | PublicRef
    ) -> tuple[Booking, Group]:
        """Return the booking behind a token or an authorised reference, with its group.

        Raises:
            DomainError: ``booking_not_found``.
        """
        if isinstance(management_token, PublicRef):
            booking = await tx.get_booking_by_public_ref(management_token.value)
        else:
            booking = await tx.get_booking_by_token_hash(hash_token(management_token))
            if booking is not None and not token_matches(management_token, booking.token_hash):
                booking = None
        if booking is None:
            raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
        group = await tx.get_group_by_id(booking.group_id)
        if group is None:
            raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
        return booking, group


def _id(booking: Booking) -> int:
    """Return the id of a stored booking."""
    if booking.id is None:  # pragma: no cover - stored bookings always have an id
        raise ValueError("booking has no id")
    return booking.id
