"""Recording what Webex and WxCC report, and showing it to admins (ADR 0010, 0017, 0018).

Webhook handlers call ``record_meeting_event`` and ``record_task_event``; jobs call
``record``. Each call writes the event and the updated ``CallStatus`` in one transaction.
Admins see a booking's timeline only with ``bookings.view`` on its group (ADR 0014).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.domain.admin import AdminService
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.booking import Booking
from app.domain.calls import (
    BookingEvent,
    BookingOverview,
    CallStatus,
    EventKind,
    apply_event,
    new_status,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, Transaction, UnitOfWork


@dataclass(frozen=True)
class Timeline:
    """A booking with its events and call status, for the admin portal."""

    booking: Booking
    events: list[BookingEvent]
    status: CallStatus


class IntegrationService:
    """Records what the platforms report and shows it to admins.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises admin views (``bookings.view``) and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    async def record(
        self, booking_id: int, kind: EventKind, at: datetime | None = None, detail: str = ""
    ) -> None:
        """Add an event to the booking's timeline and update its call status."""
        async with self._uow.locked() as tx:
            await self._record(tx, booking_id, kind, at or self._clock.now(), detail)

    async def record_meeting_event(
        self, meeting_id: str, kind: EventKind, at: datetime, detail: str = ""
    ) -> bool:
        """Record a meeting event; ``False`` for meetings OpenPlanner does not know.

        Webex may report an instance id (``<meetingId>_I_<n>``); it is matched on the series.
        """
        series = meeting_id.split("_I_", 1)[0]
        async with self._uow.locked() as tx:
            booking = await tx.get_booking_by_meeting(series)
            if booking is None or booking.id is None:
                return False
            await self._record(tx, booking.id, kind, at, detail)
        return True

    async def record_task_event(
        self, task_id: str, kind: EventKind, at: datetime, detail: str = ""
    ) -> bool:
        """Record a work item event; ``False`` for tasks OpenPlanner does not know."""
        async with self._uow.locked() as tx:
            booking = await tx.get_booking_by_task(task_id)
            if booking is None or booking.id is None:
                return False
            await self._record(tx, booking.id, kind, at, detail)
        return True

    async def status(self, booking_id: int) -> CallStatus:
        """Return a booking's call status (the citizen's waiting page; no admin data)."""
        async with self._uow.read() as tx:
            return await tx.get_call_status(booking_id) or new_status(booking_id)

    async def day_overview(
        self, principal: Principal, slug: str, day: date
    ) -> list[BookingOverview]:
        """Return the group's bookings of ``day`` (group time) with their call status.

        Raises:
            DomainError: ``group_not_found`` or ``not_permitted``.
        """
        permission = Permission.BOOKINGS_VIEW
        authorization = await self._admin.authorize(principal, permission, group_slug=slug)
        if not authorization.decision.allowed or authorization.group is None:
            raise DomainError(ErrorCode.NOT_PERMITTED)
        group = authorization.group.group
        lower = datetime.combine(day, time(0), tzinfo=group.tz).astimezone(UTC)
        upper = lower + timedelta(days=1)
        async with self._uow.read() as tx:
            return await tx.list_bookings(scope_for(principal, permission), group.id, lower, upper)

    async def timeline(self, principal: Principal, booking_id: int) -> Timeline:
        """Return the booking's timeline if ``principal`` may view its group's bookings.

        Raises:
            DomainError: ``booking_not_found`` or ``not_permitted``.
        """
        async with self._uow.read() as tx:
            booking = await tx.get_booking(booking_id)
            group = await tx.get_group_by_id(booking.group_id) if booking else None
        if booking is None or group is None:
            raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
        authorization = await self._admin.authorize(
            principal, Permission.BOOKINGS_VIEW, group_slug=group.slug
        )
        if not authorization.decision.allowed:
            raise DomainError(ErrorCode.NOT_PERMITTED)
        async with self._uow.read() as tx:
            events = await tx.get_booking_events(booking_id)
            status = await tx.get_call_status(booking_id) or new_status(booking_id)
        return Timeline(booking, events, status)

    async def _record(
        self, tx: Transaction, booking_id: int, kind: EventKind, at: datetime, detail: str
    ) -> None:
        """Write the event and the updated status in the caller's transaction."""
        await tx.add_booking_event(BookingEvent(booking_id, at, kind, detail[:128]))
        status = await tx.get_call_status(booking_id) or new_status(booking_id)
        await tx.save_call_status(apply_event(status, kind, at))
