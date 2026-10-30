"""The agenda's live meeting status: webhooks first, the provider's answer as fallback (ADR 0010).

Webex webhooks keep a booking's ``CallStatus`` current. A webhook can be lost, and the one that
says "meeting started" may never come, so when an admin opens the agenda (and every time its
live region asks again) a status that has not been touched for the refresh window is asked of
the provider through ``VideoProvider.meeting_status``. Only bookings that can have a running
meeting are asked: confirmed, with a meeting, inside the join window, not ended.

The provider is not hammered: at most ``MAX_POLLS_PER_VIEW`` bookings per view, at most
``MAX_PARALLEL`` requests at once, and a booking is asked once per window per process whether
the provider answered or not, so a failing provider is asked again after the window, not on
every poll of every open page. A failure is written to the booking's timeline with the
provider's own reason (ADR 0065), once per distinct failure. No participant names or
addresses are stored or logged: the adapter reduces the answer to ``MeetingStatus``.
"""

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta

from app.domain.authorization import Principal
from app.domain.booking import Booking
from app.domain.calls import (
    JOIN_CLOSES_AFTER,
    JOIN_OPENS_BEFORE,
    BookingEvent,
    BookingOverview,
    CallState,
    EventKind,
    apply_polled,
    new_status,
    polled_events,
)
from app.domain.integration import IntegrationService
from app.domain.ports import REASON_LIMIT, Clock, IntegrationError, UnitOfWork, VideoProvider

MAX_POLLS_PER_VIEW = 25
"""Bookings asked of the provider per view. A protection, not a tunable: raising it only
multiplies the load on Webex; a group has few meetings inside the join window at once."""

MAX_PARALLEL = 5
"""Requests to the provider in flight at once for one view."""

POLLED = "polled"
"""Detail of the timeline events a poll adds, so they differ from a webhook's."""


class MeetingStatusService:
    """Shows a group's day with call statuses that are at most one window old.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        integration: Authorises the view (``bookings.view``) and records failures.
        video: Returns the video provider now, or ``None`` when Webex is not configured; a
            function because the adapters are rebuilt when the settings change (ADR 0042).
        window: Returns the refresh window (``MEETING_STATUS_REFRESH_SECONDS``).
        version: ``APP_VERSION`` of this process, stamped on the events a poll adds.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        integration: IntegrationService,
        video: Callable[[], VideoProvider | None],
        window: Callable[[], timedelta],
        version: str = "",
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._integration = integration
        self._video = video
        self._window = window
        self._version = version
        self._asked: dict[int, datetime] = {}
        self._failed: dict[int, tuple[str, datetime]] = {}

    async def day_overview(
        self, principal: Principal, slug: str, day: date
    ) -> list[BookingOverview]:
        """Return the group's bookings of ``day`` with their call status, refreshed if stale.

        Raises:
            DomainError: ``group_not_found`` or ``not_permitted`` (``bookings.view``).
        """
        overview = await self._integration.day_overview(principal, slug, day)
        provider = self._video()
        if provider is None:
            return overview
        now = self._clock.now()
        window = self._window()
        self._forget(now, window)
        due = [o for o in overview if self._due(o, now, window)][:MAX_POLLS_PER_VIEW]
        if not due:
            return overview
        for item in due:
            self._asked[item.booking.id or 0] = now
        async with self._uow.read() as tx:
            group = await tx.get_group_by_id(due[0].booking.group_id)
        if group is None or group.video_host_email is None:
            return overview
        gate = asyncio.Semaphore(MAX_PARALLEL)

        async def ask(item: BookingOverview) -> BookingOverview:
            async with gate:
                return await self._poll(provider, item, group.video_host_email or "", now)

        refreshed = {o.booking.id: o for o in await asyncio.gather(*(ask(i) for i in due))}
        return [refreshed.get(o.booking.id, o) for o in overview]

    def _forget(self, now: datetime, window: timedelta) -> None:
        """Drop what was remembered about bookings not asked for longer than a window.

        A remembered failure goes when the booking's join window has closed, so a provider
        that stays down does not grow the memory.
        """
        for booking_id in [b for b, at in self._asked.items() if now - at >= window]:
            del self._asked[booking_id]
        for booking_id in [b for b, (_, until) in self._failed.items() if now > until]:
            del self._failed[booking_id]

    def _due(self, item: BookingOverview, now: datetime, window: timedelta) -> bool:
        """Return whether the provider is asked about this booking now."""
        booking, status = item.booking, item.status
        return (
            booking.id is not None
            and booking.is_active
            and booking.meeting is not None
            and status.call in (CallState.SCHEDULED, CallState.IN_PROGRESS)
            and booking.start - JOIN_OPENS_BEFORE <= now <= booking.end + JOIN_CLOSES_AFTER
            and (status.updated_at is None or now - status.updated_at >= window)
            and booking.id not in self._asked
        )

    async def _poll(
        self, provider: VideoProvider, item: BookingOverview, host_email: str, now: datetime
    ) -> BookingOverview:
        """Ask the provider about one booking and store the answer or the failure."""
        booking = item.booking
        booking_id, meeting = booking.id or 0, booking.meeting
        if meeting is None:  # pragma: no cover - _due checked it
            return item
        try:
            polled = await provider.meeting_status(meeting.id, host_email)
        except IntegrationError as error:
            await self._record_failure(booking, error, now)
            return item
        self._failed.pop(booking_id, None)
        async with self._uow.locked() as tx:
            before = await tx.get_call_status(booking_id) or new_status(booking_id)
            after = apply_polled(before, polled, now)
            for kind in polled_events(before, after):
                await tx.add_booking_event(
                    BookingEvent(booking_id, now, kind, POLLED, version=self._version)
                )
            await tx.save_call_status(after)
        return BookingOverview(booking, after)

    async def _record_failure(
        self, booking: Booking, error: IntegrationError, now: datetime
    ) -> None:
        """Write the provider's failure to the timeline once, not on every poll."""
        booking_id = booking.id or 0
        remembered = self._failed.get(booking_id)
        if remembered is not None and remembered[0] == error.code:
            return
        self._failed[booking_id] = (error.code, booking.end + JOIN_CLOSES_AFTER)
        await self._integration.record(
            booking_id,
            EventKind.INTEGRATION_ERROR,
            now,
            detail=f"{POLLED}:{error.code}",
            message=error.reason[:REASON_LIMIT],
        )
