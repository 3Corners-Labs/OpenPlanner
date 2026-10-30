"""Planner use cases: change existing bookings from the group's agenda (ADR 0044).

Everything needs ``bookings.manage`` on the group (the matrix, ADR 0014; a custom "Planner"
role per ADR 0039 can hold it). Each action runs in one locked transaction with its audit
event, which names the action, the booking id and the reason code, never personal data. The
participant is always told (a protection, not a setting): by email, and by SMS as well when they
agreed to it (ADR 0022).

    move    another free slot of the same group; a move that would cause a shortfall
            needs the planner's explicit confirmation
    cancel  with one of the fixed reasons (``ChangeReason``)
    rebook  ask the participant to choose a new time, with a fixed reason: the place is freed,
            one reminder follows, an unanswered request lapses (``booking_changes``)
    rebook a slot  the same for the last-made bookings of a short slot, as many as the
            shortfall, after the planner confirmed exactly that list
    accept  "leave as is" for one slot's shortfall, until its counts change
    waiting the group's bookings awaiting a new time, for the agenda's list

A planner never changes a booking that starts within the group's planner lead, that has been
dispatched to the contact center, or that is not confirmed (``booking_changes``). When one
planner makes more than ``alert_per_hour`` changes in an hour, a system event warns the
group's admins (abuse case: a compromised planner account). It never blocks.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from app.domain import booking_changes
from app.domain.admin import AdminService, Authorization, require
from app.domain.agenda import AgendaSlot, ShortfallAcceptance
from app.domain.agenda_service import agenda_days
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.booking import Booking, BookingStatus, ChangeReason
from app.domain.errors import DomainError, ErrorCode
from app.domain.lookups import group_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.rebooking import last_made
from app.domain.scheduling import Group
from app.domain.services import BookingService
from app.domain.tunable import value_for, value_of

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
        bookings: Places the moved booking (shared with the participant's reschedule).
        alert_per_hour: ``planner_changes_alert_per_hour`` (class C).
        rebook_days: ``planner_rebook_days`` (class D): a request lapses this long after the
            booking's start.
        reminder_days: ``planner_rebook_reminder_days`` (class D): the one reminder goes this
            long before the request lapses.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        bookings: BookingService,
        alert_per_hour: int | Callable[[], int],
        *,
        rebook_days: int | Callable[[int], int] = 14,
        reminder_days: int | Callable[[int], int] = 3,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._bookings = bookings
        self._alert_per_hour = alert_per_hour
        self._rebook_days = rebook_days
        self._reminder_days = reminder_days

    def rebook_days(self, organisation_id: int) -> int:
        """Days after the start after which an unanswered request to rebook lapses."""
        return value_for(self._rebook_days, organisation_id)

    def reminder_days(self, organisation_id: int) -> int:
        """Days before the lapse at which the one reminder goes."""
        return value_for(self._reminder_days, organisation_id)

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

        The participant gets the change message with the new time and a new management link.

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
        """Cancel a booking with a fixed reason; the participant gets the cancellation message.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found``, ``booking_not_active`` or
                ``planner_too_late``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            booking = await self._changeable(tx, group, booking_id)
            now = self._clock.now()
            await booking_changes.cancel(
                tx, booking, now, reason=reason, sms=True, team_invite=group.sends_team_invite
            )
            detail = f"planner.cancel,reason:{reason.value}"
            await self._audit(tx, principal, authorization, detail, booking_id)

    async def request_rebook(
        self, principal: Principal, slug: str, booking_id: int, reason: ChangeReason
    ) -> Booking:
        """Ask the participant to choose a new time; the booking frees its place at once.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found``, ``booking_not_active`` or
                ``planner_too_late``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            booking = await self._changeable(tx, group, booking_id)
            waiting = await self._request(tx, group, booking, reason)
            detail = f"planner.rebook,reason:{reason.value}"
            await self._audit(tx, principal, authorization, detail, booking_id)
        return waiting

    async def slot_candidates(
        self, principal: Principal, slug: str, start: datetime
    ) -> list[Booking]:
        """Return the bookings a bulk request for the slot at ``start`` would concern.

        The last-made confirmed bookings that a planner may still change, as many as the
        slot's open shortfall (fewer when not enough can still be changed).

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``slot_unavailable`` when
                the slot has no open shortfall (any more).
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            return await self._candidates(tx, group, start)

    async def rebook_slot(
        self,
        principal: Principal,
        slug: str,
        start: datetime,
        reason: ChangeReason,
        confirmed: frozenset[int],
    ) -> list[Booking]:
        """Ask the participants of the slot's last-made bookings to choose a new time.

        ``confirmed`` holds the ids the planner saw on the confirmation page; when the slot
        changed since, nothing happens and ``slot_changed`` asks to confirm again.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``slot_unavailable`` or
                ``slot_changed``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            candidates = await self._candidates(tx, group, start)
            if frozenset(_ids(candidates)) != confirmed:
                raise DomainError(ErrorCode.SLOT_CHANGED)
            waiting = []
            for booking in candidates:
                waiting.append(await self._request(tx, group, booking, reason))
                detail = f"planner.rebook,reason:{reason.value},bulk"
                await self._audit(tx, principal, authorization, detail, booking.id)
        return waiting

    async def waiting(self, principal: Principal, slug: str) -> list[Booking]:
        """Return the group's bookings awaiting a new time from the participant, earliest first.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            scope = scope_for(principal, Permission.BOOKINGS_MANAGE)
            return await tx.list_suspended_bookings(scope, group.id, BookingStatus.AWAITING_REBOOK)

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

    async def _request(
        self, tx: Transaction, group: Group, booking: Booking, reason: ChangeReason
    ) -> Booking:
        """Put ``booking`` on hold for a new time, with this service's periods."""
        return await booking_changes.request_rebook(
            tx,
            booking,
            self._clock.now(),
            reason=reason,
            rebook_days=self.rebook_days(group.organisation_id),
            reminder_days=self.reminder_days(group.organisation_id),
            team_invite=group.sends_team_invite,
        )

    async def _candidates(self, tx: Transaction, group: Group, start: datetime) -> list[Booking]:
        """Return the last-made changeable bookings of the slot, as many as its shortfall."""
        slot = await self._slot(tx, group, start)
        now = self._clock.now()
        if slot is None or not slot.open_shortfall or slot.start < now:
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        confirmed = await tx.find_confirmed_bookings(
            group.id, slot.start, slot.start + timedelta(seconds=1)
        )
        changeable = [b for b in confirmed if booking_changes.planner_may_change(b, group, now)]
        return last_made(changeable, slot.open_shortfall)

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
        if count == value_of(self._alert_per_hour) + 1:
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


def _ids(bookings: list[Booking]) -> list[int]:
    """Return the ids of stored bookings."""
    return [b.id for b in bookings if b.id is not None]
