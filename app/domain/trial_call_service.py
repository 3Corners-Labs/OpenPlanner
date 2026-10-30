"""Opening and joining a trial call on Webex (ADR 0071); the rules are in ``trial_call``.

    view   ─► state of the booking's trial call (read only)
    open   ─► AVAILABLE: schedule the meeting at Webex ─► locked tx: store, count, plan the
              delete job ─► record ``trial_created``; ACTIVE: reuse; anything else: unchanged
    join   ─► the link of an ACTIVE trial call, recorded as ``trial_joined``

The meeting is scheduled outside the write lock (a slow Webex must not hold the database). If
two requests race, the second finds the first one stored and deletes its own meeting again. A
failure at Webex is recorded on the booking's timeline with Webex's reason and shown to the
citizen as "not possible now"; it does not count as a trial call.
"""

from dataclasses import dataclass
from datetime import datetime

from app.domain.booking import Booking, MeetingRef, TrialCall
from app.domain.calls import EventKind, MeetingRequest
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType, PlannedJob
from app.domain.ports import Clock, IntegrationError, Transaction, UnitOfWork, VideoProvider
from app.domain.scheduling import Group
from app.domain.tokens import hash_token, token_matches
from app.domain.trial_call import CLEANUP_DELAY, TrialState, trial_request, trial_state


@dataclass(frozen=True)
class TrialView:
    """What the trial call page shows: the group, the state and, while open, when it closes.

    ``failed`` is set when this very request tried to open one and Webex refused or could not
    be reached. ``calls_left`` counts the trial calls the booking may still open.
    """

    group: Group
    booking: Booking
    state: TrialState
    expires_at: datetime | None = None
    calls_left: int = 0
    failed: bool = False


class TrialCallService:
    """Opens trial calls for citizens.

    Args:
        uow: Reads and writes bookings and jobs.
        video: Video provider, or ``None`` when Webex is not configured.
        integration: Records events on the booking's timeline.
        clock: Source of the current time.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        video: VideoProvider | None,
        integration: IntegrationService,
        clock: Clock,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._video = video
        self._integration = integration
        self._clock = clock

    async def view(self, management_token: str) -> TrialView:
        """Return the state of the booking's trial call.

        Raises:
            DomainError: ``booking_not_found``.
        """
        async with self._uow.read() as tx:
            booking, group = await self._load(tx, management_token)
        return self._view(booking, group)

    async def open(self, management_token: str) -> TrialView:
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
        request = trial_request(group, now)
        try:
            meeting = await self._schedule(request)
        except IntegrationError as error:
            await self._integration.record(
                _id(booking), EventKind.TRIAL_FAILED, detail=error.code, message=error.reason
            )
            return TrialView(
                group, booking, TrialState.AVAILABLE, calls_left=view.calls_left, failed=True
            )
        trial = TrialCall(meeting, request.end)
        async with self._uow.locked() as tx:
            current, _ = await self._load(tx, management_token)
            if trial_state(current, group, now) is not TrialState.AVAILABLE:
                stored = False
            else:
                await tx.save_trial_call(_id(current), trial)
                await tx.add_jobs(
                    _id(current),
                    [PlannedJob(JobType.DELETE_TRIAL_CALL, trial.expires_at + CLEANUP_DELAY, None)],
                )
                stored = True
        if not stored:
            await self._video_delete(meeting.id, group)
        else:
            await self._integration.record(_id(booking), EventKind.TRIAL_CREATED)
        async with self._uow.read() as tx:
            booking, group = await self._load(tx, management_token)
        return self._view(booking, group)

    async def join_link(self, management_token: str) -> str | None:
        """Return the link into the open trial call and record that it was followed.

        ``None`` when there is no open trial call (the caller sends the citizen back).

        Raises:
            DomainError: ``booking_not_found``.
        """
        async with self._uow.read() as tx:
            booking, group = await self._load(tx, management_token)
        link = booking.trial.meeting.link if booking.trial else None
        if trial_state(booking, group, self._clock.now()) is not TrialState.ACTIVE or not link:
            return None
        if not link.startswith("https://"):  # the link comes from Webex; never redirect elsewhere
            return None
        await self._integration.record(_id(booking), EventKind.TRIAL_JOINED)
        return link

    def _view(self, booking: Booking, group: Group) -> TrialView:
        """Return the page's view of ``booking`` now."""
        state = trial_state(booking, group, self._clock.now())
        if self._video is None and state in (TrialState.AVAILABLE, TrialState.ACTIVE):
            state = TrialState.OFF
        expires = booking.trial.expires_at if booking.trial and state is TrialState.ACTIVE else None
        left = max(0, group.trial_calls_per_booking - booking.trial_count)
        return TrialView(group, booking, state, expires, left)

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

    async def _load(self, tx: Transaction, management_token: str) -> tuple[Booking, Group]:
        """Return the booking behind a token with its group, or raise ``booking_not_found``."""
        booking = await tx.get_booking_by_token_hash(hash_token(management_token))
        if booking is None or not token_matches(management_token, booking.token_hash):
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
