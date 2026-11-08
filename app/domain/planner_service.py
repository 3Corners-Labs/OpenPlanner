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

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain import booking_changes, rebooking
from app.domain.admin import AdminService, Authorization, require
from app.domain.agenda import AgendaSlot, ShortfallAcceptance
from app.domain.agenda_service import agenda_days
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.booking import Booking, BookingStatus, ChangeReason, stored_id
from app.domain.booking_changes import ProposalTiming
from app.domain.errors import DomainError, ErrorCode
from app.domain.lookups import group_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.rebooking import DEFAULT_REACH, Reach, last_made, may_take, search_window
from app.domain.reschedule_proposal import (
    DEFAULT_PROPOSAL_HOURS,
    HeldProposal,
    ProposalHours,
    ProposalOutcome,
)
from app.domain.scheduling import Group
from app.domain.services import BookingService
from app.domain.slot_close import shortfall_resolved
from app.domain.tunable import value_for, value_of
from app.domain.usage import ActorClass

ALERT_ACTION = "planner.changes_alert"
"""System event key of the warning about many changes by one planner."""

ALERT_WINDOW = timedelta(hours=1)
"""The period ``planner_changes_alert_per_hour`` counts over (fixed: it is per hour)."""


CONFIRMED_BY_STAFF = "booking.confirmed_by_staff"
"""The audit detail of a planner confirming for the participant (ADR 0056 point 11)."""


MAX_OPTIONS = 12
"""How many free times a proposal row offers to choose from, best first: a presentation limit
of the form, not a setting (the reach settings bound the search)."""


PROPOSAL_SPAN = timedelta(days=3650)
"""How far around now the waiting list looks for open proposals: every one (a proposal lies
between its making and the proposed slot, both near now)."""

MAX_REVIEW_DAYS = 7
"""The longest review: a week (ADR 0057 point 5, "per shortfall slot, day or week")."""


@dataclass(frozen=True)
class ReviewScope:
    """What a review covers: one short slot, or every short slot of some local days.

    Attributes:
        start: The one slot's start (UTC), or ``None`` for whole days.
        day: The first local day of a review of days.
        days: How many days, 1 (a day) up to ``MAX_REVIEW_DAYS`` (a week).
    """

    start: datetime | None = None
    day: date | None = None
    days: int = 1

    @classmethod
    def slot(cls, start: datetime) -> "ReviewScope":
        """Return the review of the one slot at ``start``."""
        return cls(start=start)

    @classmethod
    def period(cls, day: date, days: int) -> "ReviewScope":
        """Return the review of ``days`` local days from ``day``.

        Raises:
            DomainError: ``invalid_range`` for less than one day or more than a week.
        """
        if not 1 <= days <= MAX_REVIEW_DAYS:
            raise DomainError(ErrorCode.INVALID_RANGE)
        return cls(day=day, days=days)

    def first_day(self, group: Group) -> date:
        """Return the first local day the review reads."""
        if self.start is not None:
            return self.start.astimezone(group.tz).date()
        if self.day is None:
            raise ValueError("a review covers a slot or days")
        return self.day

    def covers(self, start: datetime) -> bool:
        """Return whether the slot at ``start`` is part of the review."""
        return self.start is None or start == self.start


@dataclass(frozen=True)
class ProposalRow:
    """One booking on the review page: the assistant's choice and what may replace it.

    Attributes:
        booking: The booking that has to move.
        proposed: The assistant's proposed start, or ``None``: no slot fits, ask to rebook.
        options: Free starts the planner may choose instead, best first.
    """

    booking: Booking
    proposed: datetime | None
    options: tuple[datetime, ...]


@dataclass(frozen=True)
class Waiting:
    """A booking awaiting a new time, with its open proposal if it has one (ADR 0057)."""

    booking: Booking
    proposal: HeldProposal | None


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
        proposal_hours: ``reschedule_proposal_confirm_hours`` and
            ``reschedule_proposal_reminder_hours`` (class D, ADR 0057).
        reach: ``reschedule_search_days`` and ``reschedule_time_tolerance_minutes`` (class D,
            ADR 0057): where the assistant looks for new times.
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
        proposal_hours: ProposalHours | Callable[[int], ProposalHours] = DEFAULT_PROPOSAL_HOURS,
        reach: Reach | Callable[[int], Reach] = DEFAULT_REACH,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._bookings = bookings
        self._alert_per_hour = alert_per_hour
        self._rebook_days = rebook_days
        self._reminder_days = reminder_days
        self._proposal_hours = proposal_hours
        self._reach = reach

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
            receipt = await self._bookings.relocate(
                tx, booking, group, new_start, sms=True, actor=ActorClass.PLANNER
            )
            await self._audit(tx, principal, authorization, "planner.move", booking_id)
        return receipt.booking

    async def confirm(self, principal: Principal, slug: str, booking_id: int) -> Booking:
        """Confirm a pending booking on the participant's behalf (ADR 0056 point 11).

        For example when the participant phones. The participant gets the same full
        confirmation as by their own link; the confirmation is audited as
        ``booking.confirmed_by_staff``. After ``confirm_by`` the booking lapses instead.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found`` or ``booking_not_pending``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            booking = await self._own_booking(tx, group, booking_id)
            if not booking.awaits_confirmation:
                raise DomainError(ErrorCode.BOOKING_NOT_PENDING)
            now = self._clock.now()
            result = await self._bookings.confirm_in(tx, booking, group, now, ActorClass.PLANNER)
            if result.lapsed_unconfirmed:  # past confirm_by: lapsed, the caller says so
                return result
            await self._audit(tx, principal, authorization, CONFIRMED_BY_STAFF, booking_id)
        return result

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
                tx,
                booking,
                group,
                now,
                actor=ActorClass.PLANNER,
                reason=reason,
                sms=True,
                team_invite=group.sends_team_invite,
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

    async def propose(
        self,
        principal: Principal,
        slug: str,
        booking_id: int,
        start: datetime,
        reason: ChangeReason,
    ) -> HeldProposal:
        """Propose a new time for a confirmed booking and hold it there (ADR 0057 point 1).

        The fifth action next to move, ask to rebook, cancel and leave as is, under the same
        right and limits; the booking awaits a new time at once, and the participant confirms,
        chooses another time or cancels.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found``, ``booking_not_active``,
                ``planner_too_late``, ``same_slot``, ``booking_locked`` (the proposed slot is
                too close) or any error of a booking for the proposed slot.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            booking = await self._changeable(tx, group, booking_id)
            proposal = await self._propose(tx, group, booking, start, reason)
            detail = f"planner.propose,reason:{reason.value}"
            await self._audit(tx, principal, authorization, detail, booking_id)
        return proposal

    async def _propose(
        self, tx: Transaction, group: Group, booking: Booking, start: datetime, reason: ChangeReason
    ) -> HeldProposal:
        """Hold ``start`` for ``booking`` with this service's periods.

        The proposed slot must have a free agent now (ADR 0057 point 3: agents − booked, held
        proposals counted), besides room under its capacity (``propose_in``), so a proposal
        never makes a new shortfall, as a planner's move never does unasked.

        Raises:
            DomainError: ``slot_full`` when no agent is free there, or any error of
                ``propose_in``.
        """
        if start != booking.start and may_take(group, start, self._clock.now()):
            # The same slot or one too close gets ``propose_in``'s own refusal.
            slot = await self._slot(tx, group, start)
            if slot is None or slot.closed or slot.booked >= slot.agents:
                raise DomainError(ErrorCode.SLOT_FULL)
        return await self._bookings.propose_in(
            tx, booking, group, start, reason=reason, timing=self._timing(group)
        )

    async def _assist(
        self, tx: Transaction, group: Group, movers: list[Booking]
    ) -> list[ProposalRow]:
        """Return the assistant's proposal and the free times for each of ``movers``.

        The agenda is read around the movers' days within the organisation's reach; held
        proposals count as taken (``agenda_days``), so two proposals never share a last place.
        """
        now = self._clock.now()
        reach = value_for(self._reach, group.organisation_id)
        days = [b.start.astimezone(group.tz).date() for b in movers]
        window = search_window(group, days, reach, now)
        if window is None:
            return [ProposalRow(b, None, ()) for b in movers]
        first, last = window
        agenda = await agenda_days(tx, group, first, (last - first).days + 1, now)
        slots = [slot for day in agenda for slot in day.slots]
        proposals = rebooking.propose(group, movers, slots, reach, now)
        chosen = {p.booking_id: p.new_start for p in proposals}
        return [
            ProposalRow(
                b,
                chosen.get(stored_id(b)),
                tuple(
                    s.start for s in rebooking.ranked(group, b.start, slots, reach, now, b.subject)
                )[:MAX_OPTIONS],
            )
            for b in movers
        ]

    async def _own_booking(self, tx: Transaction, group: Group, booking_id: int) -> Booking:
        """Return the booking ``booking_id`` of ``group``; another group's is unknown."""
        booking = await tx.get_booking(booking_id)
        if booking is None or booking.group_id != group.id:  # never another group's booking
            raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
        return booking

    def _timing(self, group: Group) -> ProposalTiming:
        """Return the organisation's periods for a proposal."""
        organisation_id = group.organisation_id
        return ProposalTiming(
            rebook_days=self.rebook_days(organisation_id),
            rebook_reminder_days=self.reminder_days(organisation_id),
            hours=value_for(self._proposal_hours, organisation_id),
        )

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
    ) -> int:
        """Ask the participants of the slot's last-made bookings to choose a new time.

        ``confirmed`` holds the ids the planner saw on the confirmation page; the same send as
        a review with every booking asked (``send_review``). Returns how many were asked.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``slot_unavailable`` or
                ``slot_changed``.
        """
        choices = dict.fromkeys(confirmed)
        return await self.send_review(principal, slug, ReviewScope.slot(start), reason, choices)

    async def waiting(self, principal: Principal, slug: str) -> list[Waiting]:
        """Return the group's bookings awaiting a new time, earliest first, with any proposal.

        A proposal is shown while it holds its place, also when its deadline passed and the
        worker has not released it yet (its "held until" is then in the past).

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            scope = scope_for(principal, Permission.BOOKINGS_MANAGE)
            waiting = await tx.list_suspended_bookings(
                scope, group.id, BookingStatus.AWAITING_REBOOK
            )
            now = self._clock.now()
            held = await tx.find_open_proposals(group.id, now - PROPOSAL_SPAN, now + PROPOSAL_SPAN)
        by_booking = {p.booking_id: p for p in held}
        return [Waiting(b, by_booking.get(stored_id(b))) for b in waiting]

    async def booking_proposal(
        self, principal: Principal, slug: str, booking_id: int
    ) -> ProposalRow:
        """Return the assistant's proposal for one confirmed booking, with the free times.

        For the per-booking action "propose a new time" (ADR 0057 point 1).

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found``, ``booking_not_active`` or
                ``planner_too_late``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            booking = await self._changeable(tx, group, booking_id)
            if not booking.is_active:
                raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
            (row,) = await self._assist(tx, group, [booking])
        return row

    async def review_proposals(
        self, principal: Principal, slug: str, scope: ReviewScope
    ) -> list[ProposalRow]:
        """Return the assistant's proposals for the excess bookings in ``scope`` (point 5).

        Per short slot the last-made confirmed bookings a planner may still change, as many as
        its open shortfall; each with the proposed time (or none: ask to rebook) and the free
        times the planner may choose instead. One slot, a day or a week; the proposals are
        assigned together, so two never share a last place.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``slot_unavailable`` when
                nothing in ``scope`` is short (any more).
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            movers = [b for b in await self._movers(tx, group, scope) if b.is_active]
            return await self._assist(tx, group, movers)

    async def send_review(
        self,
        principal: Principal,
        slug: str,
        scope: ReviewScope,
        reason: ChangeReason,
        choices: Mapping[int, datetime | None],
    ) -> int:
        """Send what the planner reviewed in one transaction; return how many went.

        ``choices`` maps each ticked booking to the time the planner kept or chose, or to
        ``None`` to ask that participant to choose a time themselves (ADR 0044). Every booking
        must still be among the excess of ``scope`` and every chosen time must still have a
        free agent and room under the write lock; otherwise nothing is sent and
        ``slot_changed`` asks the planner to review again (ADR 0057 point 5: never sent blind).

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``slot_unavailable`` or
                ``slot_changed``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            short = await self._short_slots(tx, group, scope)
            movers = {stored_id(b): b for b in await self._movers(tx, group, scope, short)}
            if not choices or not set(choices) <= set(movers):
                raise DomainError(ErrorCode.SLOT_CHANGED)
            for booking_id, chosen in choices.items():
                booking = movers[booking_id]
                if chosen is None:
                    await self._request(tx, group, booking, reason)
                    detail = f"planner.rebook,reason:{reason.value},bulk"
                else:
                    try:
                        await self._propose(tx, group, booking, chosen, reason)
                    except DomainError as error:  # the time no longer fits: review again
                        raise DomainError(ErrorCode.SLOT_CHANGED) from error
                    detail = f"planner.propose,reason:{reason.value},bulk"
                await self._audit(tx, principal, authorization, detail, booking_id)
            for slot in short:
                after = await self._slot(tx, group, slot.start)
                if after is not None and not after.shortfall:  # ADR 0117 metric C5
                    resolved = shortfall_resolved(group, slot.start, after.agents, after.booked)
                    await tx.record_usage(resolved)
        return len(choices)

    async def withdraw(self, principal: Principal, slug: str, booking_id: int) -> None:
        """Take back a booking's open proposal; it keeps awaiting a new time (point 10).

        The held place is free at once; the participant can still choose a time themselves.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found`` or ``proposal_not_open``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            booking = await self._own_booking(tx, group, booking_id)
            released = await booking_changes.release_proposal(
                tx, booking, self._clock.now(), ProposalOutcome.WITHDRAWN
            )
            if released is None:
                raise DomainError(ErrorCode.PROPOSAL_NOT_OPEN)
            await self._audit(tx, principal, authorization, "planner.withdraw", booking_id)

    async def confirm_proposal(self, principal: Principal, slug: str, booking_id: int) -> Booking:
        """Take the proposed time on the participant's behalf, after a phone call (point 10).

        The booking moves as when the participant confirms by their link: the change mail
        with the calendar file goes to them. Audited; the usage fact names the planner.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found``, ``proposal_not_open`` or
                any error of the move.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            booking = await self._own_booking(tx, group, booking_id)
            receipt = await self._bookings.confirm_proposal_in(
                tx, booking, group, ActorClass.PLANNER
            )
            await self._audit(tx, principal, authorization, "planner.confirm_proposal", booking_id)
        return receipt.booking

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
            group,
            self._clock.now(),
            reason=reason,
            rebook_days=self.rebook_days(group.organisation_id),
            reminder_days=self.reminder_days(group.organisation_id),
            team_invite=group.sends_team_invite,
        )

    async def _candidates(self, tx: Transaction, group: Group, start: datetime) -> list[Booking]:
        """Return the changeable ones of the slot's last-made bookings (up to its shortfall)."""
        return await self._movers(tx, group, ReviewScope.slot(start))

    async def _short_slots(
        self, tx: Transaction, group: Group, scope: ReviewScope
    ) -> list[AgendaSlot]:
        """Return the slots of ``scope`` with an open shortfall that have not started.

        Raises:
            DomainError: ``slot_unavailable`` when there is none.
        """
        now = self._clock.now()
        agenda = await agenda_days(tx, group, scope.first_day(group), scope.days, now)
        short = [
            slot
            for day in agenda
            for slot in day.slots
            if slot.open_shortfall and slot.start >= now and scope.covers(slot.start)
        ]
        if not short:
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        return short

    async def _movers(
        self,
        tx: Transaction,
        group: Group,
        scope: ReviewScope,
        short: list[AgendaSlot] | None = None,
    ) -> list[Booking]:
        """Return, per short slot of ``scope``, its last-made bookings a planner may change.

        The last-made places are chosen among every booking in the slot, pending ones too
        (ADR 0044 §5, ADR 0056 point 11), as many as its open shortfall; of those, the ones a
        planner may change are returned. A pending booking among them is left to its own
        confirmation or lapse by the callers that need a confirmed one.
        """
        short = short if short is not None else await self._short_slots(tx, group, scope)
        now = self._clock.now()
        movers: list[Booking] = []
        for slot in short:
            holding = await tx.find_agenda_bookings(
                group.id, slot.start, slot.start + timedelta(seconds=1)
            )
            last = last_made(holding, slot.open_shortfall)
            movers += [b for b in last if booking_changes.planner_may_change(b, group, now)]
        return movers

    async def _changeable(self, tx: Transaction, group: Group, booking_id: int) -> Booking:
        """Return the group's booking ``booking_id`` if a planner may change it now."""
        booking = await self._own_booking(tx, group, booking_id)
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
