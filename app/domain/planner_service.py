"""Planner use cases: change existing bookings from the group's agenda (ADR 0044).

Everything needs ``bookings.manage`` on the group (the matrix, ADR 0014; a custom "Planner"
role per ADR 0039 can hold it). Each action runs in one locked transaction with its audit
event, which names the action, the booking id and the reason code, never personal data. The
citizen is always told (a protection, not a setting): by email, and by SMS as well when they
agreed to it (ADR 0022).

    move    another free slot of the same group; a move that would cause a shortfall
            needs the planner's explicit confirmation
    cancel  with one of the fixed reasons (``ChangeReason``)
    accept  "leave as is" for one slot's shortfall, until its counts change

A planner never changes a booking that starts within the group's planner lead, that has been
dispatched to the contact center, or that is not confirmed (``booking_changes``). When one
planner makes more than ``alert_per_hour`` changes in an hour, a system event warns the
group's admins (abuse case: a compromised planner account). It never blocks.
"""

from datetime import datetime, timedelta

from app.domain import booking_changes
from app.domain.admin import AdminService, Authorization, require
from app.domain.agenda import AgendaSlot, ShortfallAcceptance
from app.domain.agenda_service import agenda_days
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.authorization import Permission, Principal
from app.domain.booking import Booking, ChangeReason
from app.domain.errors import DomainError, ErrorCode
from app.domain.lookups import group_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group
from app.domain.services import BookingService

ALERT_ACTION = "planner.changes_alert"
"""System event key of the warning about many changes by one planner."""

ALERT_WINDOW = timedelta(hours=1)
"""The period ``planner_changes_alert_per_hour`` counts over (fixed: it is per hour)."""


class PlannerService:
    """Application service for a planner's changes to existing bookings.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises every operation and audits refusals.
        bookings: Places the moved booking (shared with the citizen's reschedule).
        alert_per_hour: ``planner_changes_alert_per_hour`` (class C).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        bookings: BookingService,
        alert_per_hour: int,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._bookings = bookings
        self._alert_per_hour = alert_per_hour

    async def booking(self, principal: Principal, slug: str, booking_id: int) -> Booking:
        """Return one booking of the group that the planner may still change (for a form).

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``booking_not_found``,
                ``booking_not_active`` or ``planner_too_late``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            return await self._changeable(tx, group, booking_id)

    async def move(
        self,
        principal: Principal,
        slug: str,
        booking_id: int,
        new_start: datetime,
        *,
        accept_shortfall: bool = False,
    ) -> Booking:
        """Move a booking to another slot of its group; return the new booking.

        The citizen gets the change message with the new time and a new management link.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found``, ``booking_not_active``,
                ``planner_too_late``, ``move_causes_shortfall`` (unless accepted) or any
                error of booking the new start (``slot_full``, ``group_closed``, …).
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            booking = await self._changeable(tx, group, booking_id)
            target = await self._slot(tx, group, new_start)
            if target is not None and target.booked + 1 > target.agents and not accept_shortfall:
                raise DomainError(ErrorCode.MOVE_CAUSES_SHORTFALL)
            receipt = await self._bookings.relocate(tx, booking, group, new_start, sms=True)
            await self._audit(tx, principal, authorization, "planner.move", booking_id)
        return receipt.booking

    async def cancel(
        self, principal: Principal, slug: str, booking_id: int, reason: ChangeReason
    ) -> None:
        """Cancel a booking with a fixed reason; the citizen gets the cancellation message.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found``, ``booking_not_active`` or
                ``planner_too_late``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            booking = await self._changeable(tx, group, booking_id)
            now = self._clock.now()
            await booking_changes.cancel(tx, booking, now, reason=reason, sms=True)
            detail = f"planner.cancel,reason:{reason.value}"
            await self._audit(tx, principal, authorization, detail, booking_id)

    async def accept(self, principal: Principal, slug: str, start: datetime) -> None:
        """Accept the shortfall of the slot at ``start`` as it is now ("leave as is").

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``slot_unavailable`` when
                the slot has no shortfall (any more) or has started.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            slot = await self._slot(tx, group, start)
            now = self._clock.now()
            if slot is None or not slot.shortfall or slot.start < now:
                raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
            await tx.save_shortfall_acceptance(
                ShortfallAcceptance(
                    group.id, slot.start, slot.agents, slot.booked, now, principal.subject
                )
            )
            await self._audit(tx, principal, authorization, "shortfall.accept", None)

    async def _changeable(self, tx: Transaction, group: Group, booking_id: int) -> Booking:
        """Return the group's booking ``booking_id`` if a planner may change it now."""
        booking = await tx.get_booking(booking_id)
        if booking is None or booking.group_id != group.id:  # never another group's booking
            raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
        booking_changes.ensure_planner_may_change(booking, group, self._clock.now())
        return booking

    async def _slot(self, tx: Transaction, group: Group, start: datetime) -> AgendaSlot | None:
        """Return the agenda slot of ``group`` that starts at ``start``, if any."""
        day = start.astimezone(group.tz).date()
        (agenda_day,) = await agenda_days(tx, group, day, 1, self._clock.now())
        return next((s for s in agenda_day.slots if s.start == start), None)

    async def _authorize(self, principal: Principal, slug: str) -> Authorization:
        """Require ``bookings.manage`` on the group."""
        return require(
            await self._admin.authorize(principal, Permission.BOOKINGS_MANAGE, group_slug=slug)
        )

    async def _audit(
        self,
        tx: Transaction,
        principal: Principal,
        authorization: Authorization,
        detail: str,
        booking_id: int | None,
    ) -> None:
        """Record the change; warn once when the planner passes the hourly threshold."""
        now = self._clock.now()
        summary = authorization.group
        organisation = summary.organisation_slug if summary else None
        group = summary.group.slug if summary else None
        action = Permission.BOOKINGS_MANAGE.value
        await tx.add_audit_event(
            AuditEvent(
                now,
                principal.subject,
                action,
                Outcome.SUCCESS,
                "",
                organisation,
                group,
                detail,
                Source.ADMIN,
                booking_id,
            )
        )
        count = await tx.count_audit_events(principal.subject, {action}, now - ALERT_WINDOW)
        if count == self._alert_per_hour + 1:
            await tx.add_audit_event(
                AuditEvent(
                    now,
                    SYSTEM_ACTOR,
                    ALERT_ACTION,
                    Outcome.FAILURE,
                    "too_many_changes",
                    organisation,
                    group,
                    f"actor:{principal.subject},changes:{count}",
                    Source.SYSTEM,
                )
            )
