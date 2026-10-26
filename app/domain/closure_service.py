"""Emergency closure use cases for admins (ADR 0024).

Every operation asks ``AdminService.authorize`` first (``closure.manage`` on a group,
``organisation.closure`` on an organisation) and records an audit event in the same locked
transaction as the change. Bookings are never deleted and keep their personal data while they
are on hold; an admin resolves them one by one or all at once.

    close_group / close_organisation
        └─ closure ─► confirmed bookings inside it ─► suspended ─► notice mail (+ purge at
                                                                    closure end + 30 days)
    update   end and notices; newly covered bookings suspended; optional update mail to all
    lift     end now; optionally reinstate what can still go ahead
    reinstate / cancel / move        one suspended booking (or all: reinstate_all, cancel_all)

Audit details are machine keys with counts (``closure.start,bookings:3``), never values.
A suspended booking can only be handled through the group it belongs to: the booking id in
the URL is checked against the authorised group, so one group's admin cannot reach another's.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.admin import AdminService, Authorization, record_change, require
from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.booking import Booking
from app.domain.closure import (
    NOTICE_LOCALES,
    Closure,
    clean_notice,
    covering,
    notice_field,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import (
    JobType,
    change_deadline,
    plan_closure_update,
    plan_for_booking,
    plan_for_cancellation,
    plan_for_suspension,
    plan_purge,
)
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, GroupSummary, Organisation
from app.domain.services import BookingService


@dataclass(frozen=True)
class ClosureInput:
    """What an admin enters to close a group.

    Attributes:
        starts_at: First closed start time (UTC); ``None`` or a past moment means now.
        ends_at: First start time that is open again (UTC); ``None`` until further notice.
        notices: The explanation for citizens per locale (``NOTICE_LOCALES`` required).
    """

    starts_at: datetime | None
    ends_at: datetime | None
    notices: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClosureOverview:
    """A group's closure page: its visible closure, the bookings on hold and what lies ahead.

    Attributes:
        closure: The active or upcoming closure, if any.
        suspended: Every suspended booking of the group, earliest first.
        confirmed_ahead: How many confirmed bookings lie ahead (what a new closure may hold).
    """

    closure: Closure | None
    suspended: list[Booking]
    confirmed_ahead: int


@dataclass(frozen=True)
class GroupClosureState:
    """One group on the organisation's closure page."""

    summary: GroupSummary
    closure: Closure | None
    suspended: int


@dataclass(frozen=True)
class GroupOutcome:
    """What closing or lifting did to one group of an organisation.

    Attributes:
        slug: The group.
        changed: Whether the group was closed (or lifted); ``False`` when it was skipped
            because it already had a closure (or had none to lift).
        bookings: How many bookings were suspended (or reinstated).
    """

    slug: str
    changed: bool
    bookings: int = 0


@dataclass(frozen=True)
class UpdateOutcome:
    """What an update did: bookings mailed the new notice, bookings newly suspended."""

    notified: int
    suspended: int


@dataclass(frozen=True)
class BulkOutcome:
    """The result of handling many suspended bookings: how many done, how many not possible."""

    done: int
    skipped: int = 0


class ClosureService:
    """Application service for emergency closures.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises every operation and audits refusals.
        bookings: Moves a booking to a new slot with the citizen's own rules.
    """

    def __init__(
        self, uow: UnitOfWork, clock: Clock, admin: AdminService, bookings: BookingService
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._bookings = bookings

    # --- one group ------------------------------------------------------------------------

    async def overview(self, principal: Principal, slug: str) -> ClosureOverview:
        """Return the group's visible closure and its suspended bookings.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        await self._authorize(principal, slug)
        now = self._clock.now()
        async with self._uow.read() as tx:
            group = await _group(tx, slug)
            closures = await tx.get_closures(group.id, now)
            suspended = await tx.list_suspended_bookings(self._scope(principal), group.id)
            ahead = await tx.find_confirmed_bookings(group.id, now, None)
        return ClosureOverview(closures[0] if closures else None, suspended, len(ahead))

    async def close_group(self, principal: Principal, slug: str, closure: ClosureInput) -> int:
        """Close the group and suspend the confirmed bookings inside the closure.

        Returns:
            The number of suspended bookings.

        Raises:
            DomainError: ``not_permitted``, ``closure_exists`` or ``invalid_configuration``
                (``ends_at`` or ``notice_<locale>``).
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await _group(tx, slug)
            count = await self._close(tx, group, closure)
            await self._audit(tx, principal, authorization, slug, f"closure.start,bookings:{count}")
        return count

    async def update(
        self,
        principal: Principal,
        slug: str,
        ends_at: datetime | None,
        notices: Mapping[str, str],
        *,
        notify: bool,
    ) -> UpdateOutcome:
        """Change the end and the notices of the group's visible closure.

        Bookings that the new end brings inside the closure are suspended (and get the
        notice); with ``notify`` every other suspended booking of the closure gets the updated
        notice by email. The purge of all of them follows the new end.

        Raises:
            DomainError: ``not_permitted``, ``closure_not_found`` or ``invalid_configuration``.
        """
        authorization = await self._authorize(principal, slug)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            group = await _group(tx, slug)
            current = await _visible(tx, group, now)
            if ends_at is not None and ends_at <= max(now, current.starts_at):
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "ends_at")
            updated = Closure(
                current.id,
                group.id,
                current.starts_at,
                ends_at,
                _notices(notices),
                current.created_at,
            )
            await tx.save_closure(updated)
            notified = 0
            for booking in await self._closure_bookings(tx, principal, group, updated):
                await self._repurge(tx, booking, updated)
                if notify:
                    await tx.add_jobs(_id(booking), plan_closure_update(now=now))
                    notified += 1
            added = await self._suspend_covered(tx, group, updated)
            detail = f"closure.update,notified:{notified},bookings:{added}"
            await self._audit(tx, principal, authorization, slug, detail)
        return UpdateOutcome(notified, added)

    async def lift(self, principal: Principal, slug: str, *, reinstate: bool) -> BulkOutcome:
        """End the group's visible closure now; optionally reinstate what can go ahead.

        Returns:
            With ``reinstate``: the bookings reinstated and those that could not be.

        Raises:
            DomainError: ``not_permitted`` or ``closure_not_found``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await _group(tx, slug)
            outcome = await self._lift(tx, principal, group, reinstate=reinstate)
            await self._audit(
                tx, principal, authorization, slug, f"closure.lift,bookings:{outcome.done}"
            )
        return outcome

    async def reinstate(self, principal: Principal, slug: str, booking_id: int) -> None:
        """Confirm a suspended booking again at its original time.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found``, ``booking_not_active``,
                ``booking_locked`` (its change deadline has passed) or ``group_closed`` (a
                closure still covers it).
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await _group(tx, slug)
            booking = await _suspended(tx, group, booking_id)
            await self._reinstate(tx, group, booking)
            await self._audit(tx, principal, authorization, slug, "booking.reinstate")

    async def reinstate_all(self, principal: Principal, slug: str) -> BulkOutcome:
        """Reinstate every suspended booking of the group that can still go ahead.

        Raises:
            DomainError: ``not_permitted``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await _group(tx, slug)
            outcome = await self._reinstate_all(tx, principal, group)
            await self._audit(
                tx, principal, authorization, slug, f"booking.reinstate_all,bookings:{outcome.done}"
            )
        return outcome

    async def cancel(self, principal: Principal, slug: str, booking_id: int) -> None:
        """Cancel a suspended booking; the citizen gets the cancellation mail.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found`` or ``booking_not_active``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await _group(tx, slug)
            await self._cancel(tx, await _suspended(tx, group, booking_id))
            await self._audit(tx, principal, authorization, slug, "booking.cancel")

    async def cancel_all(self, principal: Principal, slug: str) -> BulkOutcome:
        """Cancel every suspended booking of the group.

        Raises:
            DomainError: ``not_permitted``.
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await _group(tx, slug)
            bookings = await tx.list_suspended_bookings(self._scope(principal), group.id)
            for booking in bookings:
                await self._cancel(tx, booking)
            detail = f"booking.cancel_all,bookings:{len(bookings)}"
            await self._audit(tx, principal, authorization, slug, detail)
        return BulkOutcome(len(bookings))

    async def move(
        self, principal: Principal, slug: str, booking_id: int, new_start: datetime
    ) -> Booking:
        """Move a suspended booking to a free slot; the citizen gets the change mail.

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found``, ``booking_not_active`` or
                any error of booking the new start (``slot_full``, ``group_closed``, …).
        """
        authorization = await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            group = await _group(tx, slug)
            booking = await _suspended(tx, group, booking_id)
            receipt = await self._bookings.relocate(tx, booking, group, new_start)
            await self._audit(tx, principal, authorization, slug, "booking.move")
        return receipt.booking

    async def booking(self, principal: Principal, slug: str, booking_id: int) -> Booking:
        """Return one suspended booking of the group (for the move form).

        Raises:
            DomainError: ``not_permitted``, ``booking_not_found`` or ``booking_not_active``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            return await _suspended(tx, await _group(tx, slug), booking_id)

    # --- a whole organisation -------------------------------------------------------------

    async def organisations(self, principal: Principal) -> list[Organisation]:
        """Return the organisations ``principal`` may close as a whole."""
        async with self._uow.read() as tx:
            return await tx.list_organisations(
                scope_for(principal, Permission.ORGANISATION_CLOSURE)
            )

    async def organisation_overview(
        self, principal: Principal, organisation_slug: str
    ) -> list[GroupClosureState]:
        """Return every group of the organisation with its visible closure and bookings on hold.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize_organisation(principal, organisation_slug)
        now = self._clock.now()
        states = []
        scope = scope_for(principal, Permission.ORGANISATION_CLOSURE)
        async with self._uow.read() as tx:
            if await tx.get_organisation(organisation_slug) is None:
                raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
            for summary in await self._organisation_groups(tx, principal, organisation_slug):
                closures = await tx.get_closures(summary.group.id, now)
                suspended = await tx.list_suspended_bookings(scope, summary.group.id)
                states.append(
                    GroupClosureState(summary, closures[0] if closures else None, len(suspended))
                )
        return states

    async def close_organisation(
        self, principal: Principal, organisation_slug: str, closure: ClosureInput
    ) -> list[GroupOutcome]:
        """Close every group of the organisation that has no closure yet, all or none.

        Raises:
            DomainError: ``not_permitted`` or ``invalid_configuration``.
        """
        await self._authorize_organisation(principal, organisation_slug)
        outcomes = []
        async with self._uow.locked() as tx:
            now = self._clock.now()
            for summary in await self._organisation_groups(tx, principal, organisation_slug):
                if await tx.get_closures(summary.group.id, now):
                    outcomes.append(GroupOutcome(summary.group.slug, changed=False))
                    continue
                count = await self._close(tx, summary.group, closure)
                outcomes.append(GroupOutcome(summary.group.slug, True, count))
                await self._organisation_audit(
                    tx, principal, organisation_slug, summary, f"closure.start,bookings:{count}"
                )
        return outcomes

    async def lift_organisation(
        self, principal: Principal, organisation_slug: str, *, reinstate: bool
    ) -> list[GroupOutcome]:
        """Lift the visible closure of every group of the organisation.

        Raises:
            DomainError: ``not_permitted``.
        """
        await self._authorize_organisation(principal, organisation_slug)
        outcomes = []
        async with self._uow.locked() as tx:
            now = self._clock.now()
            for summary in await self._organisation_groups(tx, principal, organisation_slug):
                if not await tx.get_closures(summary.group.id, now):
                    outcomes.append(GroupOutcome(summary.group.slug, changed=False))
                    continue
                result = await self._lift(tx, principal, summary.group, reinstate=reinstate)
                outcomes.append(GroupOutcome(summary.group.slug, True, result.done))
                await self._organisation_audit(
                    tx,
                    principal,
                    organisation_slug,
                    summary,
                    f"closure.lift,bookings:{result.done}",
                )
        return outcomes

    # --- steps ----------------------------------------------------------------------------

    async def _close(self, tx: Transaction, group: Group, closure: ClosureInput) -> int:
        """Store a closure for ``group`` and suspend what it covers; return how many."""
        now = self._clock.now()
        starts_at = max(closure.starts_at or now, now)
        if closure.ends_at is not None and closure.ends_at <= starts_at:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "ends_at")
        if await tx.get_closures(group.id, now):
            raise DomainError(ErrorCode.CLOSURE_EXISTS)
        stored = await tx.add_closure(
            Closure(None, group.id, starts_at, closure.ends_at, _notices(closure.notices), now)
        )
        return await self._suspend_covered(tx, group, stored)

    async def _suspend_covered(self, tx: Transaction, group: Group, closure: Closure) -> int:
        """Suspend the confirmed bookings inside ``closure``; return how many."""
        now = self._clock.now()
        closure_id = _id(closure)
        bookings = await tx.find_confirmed_bookings(group.id, closure.starts_at, closure.ends_at)
        for booking in bookings:
            await tx.save_booking(booking.suspend(closure_id))
            await tx.replace_jobs(
                _id(booking), plan_for_suspension(now=now, purge_at=closure.purge_at)
            )
        return len(bookings)

    async def _lift(
        self, tx: Transaction, principal: Principal, group: Group, *, reinstate: bool
    ) -> BulkOutcome:
        """End the visible closure of ``group`` now and move the purge of its bookings."""
        now = self._clock.now()
        current = await _visible(tx, group, now)
        # An upcoming closure that is lifted never starts: its window becomes empty.
        starts_at = min(now, current.starts_at)
        ended = Closure(current.id, group.id, starts_at, now, current.notices, current.created_at)
        await tx.save_closure(ended)
        for booking in await self._closure_bookings(tx, principal, group, ended):
            await self._repurge(tx, booking, ended)
        if not reinstate:
            return BulkOutcome(0)
        return await self._reinstate_all(tx, principal, group)

    async def _reinstate_all(
        self, tx: Transaction, principal: Principal, group: Group
    ) -> BulkOutcome:
        """Reinstate every suspended booking of ``group`` that can go ahead."""
        done = skipped = 0
        for booking in await tx.list_suspended_bookings(self._scope(principal), group.id):
            try:
                await self._reinstate(tx, group, booking)
            except DomainError:
                skipped += 1
            else:
                done += 1
        return BulkOutcome(done, skipped)

    async def _reinstate(self, tx: Transaction, group: Group, booking: Booking) -> None:
        """Confirm ``booking`` again and plan its jobs as a fresh booking."""
        now = self._clock.now()
        if now >= change_deadline(booking.start, group.dispatch_lead):
            raise DomainError(ErrorCode.BOOKING_LOCKED)
        if covering(await tx.get_closures(group.id, now), booking.start) is not None:
            raise DomainError(ErrorCode.GROUP_CLOSED)
        if booking.contact is None:
            raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
        await tx.save_booking(booking.reinstate())
        jobs = plan_for_booking(
            start=booking.start,
            end=booking.end,
            contact=booking.contact,
            consents=booking.consents,
            rules=await tx.get_reminder_rules(group.id),
            now=now,
            dispatch_lead=group.dispatch_lead,
            message=JobType.SEND_REINSTATEMENT,
        )
        await tx.replace_jobs(_id(booking), jobs)

    async def _cancel(self, tx: Transaction, booking: Booking) -> None:
        """Cancel ``booking`` with the cancellation mail and the usual purge."""
        now = self._clock.now()
        await tx.save_booking(booking.cancel(now))
        await tx.replace_jobs(_id(booking), plan_for_cancellation(cancelled_at=now))

    async def _closure_bookings(
        self, tx: Transaction, principal: Principal, group: Group, closure: Closure
    ) -> list[Booking]:
        """Return the bookings ``closure`` suspended that are still on hold."""
        return [
            b
            for b in await tx.list_suspended_bookings(self._scope(principal), group.id)
            if b.closure_id == closure.id
        ]

    async def _repurge(self, tx: Transaction, booking: Booking, closure: Closure) -> None:
        """Move the booking's purge to the closure's end plus the retention period."""
        await tx.delete_pending_jobs(_id(booking), {JobType.PURGE_PII})
        await tx.add_jobs(_id(booking), plan_purge(closure.purge_at))

    # --- helpers --------------------------------------------------------------------------

    async def _authorize(self, principal: Principal, slug: str) -> Authorization:
        """Authorise ``closure.manage`` on the group."""
        return require(
            await self._admin.authorize(principal, Permission.CLOSURE_MANAGE, group_slug=slug)
        )

    async def _authorize_organisation(self, principal: Principal, slug: str) -> Authorization:
        """Authorise ``organisation.closure`` on the organisation."""
        return require(
            await self._admin.authorize(
                principal, Permission.ORGANISATION_CLOSURE, organisation_slug=slug
            )
        )

    @staticmethod
    def _scope(principal: Principal) -> Scope:
        """Return the listing scope of ``closure.manage`` (the database side of the check)."""
        return scope_for(principal, Permission.CLOSURE_MANAGE)

    @staticmethod
    async def _organisation_groups(
        tx: Transaction, principal: Principal, organisation_slug: str
    ) -> list[GroupSummary]:
        """Return the organisation's groups, inside the principal's organisation scope."""
        scope = scope_for(principal, Permission.ORGANISATION_CLOSURE)
        return [g for g in await tx.list_groups(scope) if g.organisation_slug == organisation_slug]

    async def _audit(
        self,
        tx: Transaction,
        principal: Principal,
        authorization: Authorization,
        slug: str,
        detail: str,
    ) -> None:
        """Record a closure change of one group in the same transaction."""
        await record_change(
            tx,
            self._clock.now(),
            principal,
            Permission.CLOSURE_MANAGE,
            authorization.target.organisation,
            slug,
            detail,
        )

    async def _organisation_audit(
        self,
        tx: Transaction,
        principal: Principal,
        organisation_slug: str,
        summary: GroupSummary,
        detail: str,
    ) -> None:
        """Record an organisation-wide closure change, once per group it touched."""
        await record_change(
            tx,
            self._clock.now(),
            principal,
            Permission.ORGANISATION_CLOSURE,
            organisation_slug,
            summary.group.slug,
            detail,
        )


def _notices(raw: Mapping[str, str]) -> dict[str, str]:
    """Return the cleaned notice per required locale.

    Raises:
        DomainError: ``invalid_configuration`` with ``notice_<locale>``.
    """
    return {loc: clean_notice(raw.get(loc, ""), notice_field(loc)) for loc in NOTICE_LOCALES}


async def _group(tx: Transaction, slug: str) -> Group:
    """Return the group with ``slug`` or raise ``group_not_found``."""
    group = await tx.get_group(slug)
    if group is None:
        raise DomainError(ErrorCode.GROUP_NOT_FOUND)
    return group


async def _visible(tx: Transaction, group: Group, now: datetime) -> Closure:
    """Return the group's active or upcoming closure or raise ``closure_not_found``."""
    closures = await tx.get_closures(group.id, now)
    if not closures:
        raise DomainError(ErrorCode.CLOSURE_NOT_FOUND)
    return closures[0]


async def _suspended(tx: Transaction, group: Group, booking_id: int) -> Booking:
    """Return a suspended booking of ``group``.

    Raises:
        DomainError: ``booking_not_found`` when it belongs to another group (no hint that it
            exists), ``booking_not_active`` when it is not on hold.
    """
    booking = await tx.get_booking(booking_id)
    if booking is None or booking.group_id != group.id:
        raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
    if not booking.is_suspended:
        raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
    return booking


def _id(entity: Booking | Closure) -> int:
    """Return the ID of a stored entity; a missing ID is a programming error."""
    if entity.id is None:
        raise ValueError("entity has not been stored")
    return entity.id
