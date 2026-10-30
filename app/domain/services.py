"""Booking use cases: availability, book, find, cancel, reschedule.

Every write runs in one locked transaction, so slot creation, the capacity check, the
booking and its jobs commit together or not at all (CLAUDE.md architecture rules).

    book ─► locked tx ─► _place: window ─► offered? ─► slot (create, snapshot capacity)
                                ─► capacity (minus overlapping old-grid bookings)
                                ─► address limit (same slot, max per group; ADR 0066)
                                ─► booking (pending) + token ─► jobs: request to confirm
    confirm ─► locked tx ─► pending and before confirm_by? ─► confirmed ─► jobs as booked
                                                    else ─► lapses (ADR 0056)
    reschedule ─► locked tx ─► cancel old (jobs: purge only) ─► _place new in the SAME group

A citizen's new booking awaits confirmation by email link (ADR 0056): it holds its place but
gets no meeting, reminders or task until confirmed; its deadline ``confirm_by`` follows the
organisation's ``ConfirmPolicy``. A booking that is moved or rescheduled keeps the
confirmation it had (the address was already verified), so it is placed confirmed.

A booking is read-only from its change deadline (``Group.change_deadline``: the group's
dispatch lead before the start, T−5 min by default, or with the own queue a number of working
days before, ADR 0043): cancel and reschedule then raise
``booking_locked``. A booking suspended by an emergency closure (ADR 0024) has no deadline:
the citizen can move or cancel it until it is resolved. No slot inside a closure is offered
or booked (``group_closed``), and no direct call starts while one is active. A direct call
also needs room under the group's cap of open direct calls (``direct_call_full``, ADR 0026).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.domain.availability import (
    AvailableSlot,
    available_slots,
    booking_window,
    offered_slot,
    overlapping_active,
    utc_bounds,
    validate_range,
)
from app.domain.booking import (
    DEFAULT_PHONE_REGION,
    Booking,
    BookingKind,
    BookingStatus,
    Contact,
    Slot,
)
from app.domain.booking_changes import lapse_unconfirmed
from app.domain.channels import ChannelKind
from app.domain.closure import Closure, covering
from app.domain.confirmation import DEFAULT_CONFIRM_POLICY, ConfirmPolicy, confirm_window
from app.domain.direct_queue import (
    ESTIMATE_WINDOW,
    DirectAvailability,
    QueueInfo,
    has_room,
    load_since,
    queue_info,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import FormDefinition, Purpose, validate_answers
from app.domain.holiday_service import effective_schedule
from app.domain.images import ImageFile
from app.domain.job_plan import (
    JobType,
    PlannedJob,
    plan_for_booking,
    plan_for_cancellation,
    plan_for_direct,
    plan_for_pending,
)
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, is_open
from app.domain.tokens import (
    MIN_KEY_BYTES,
    derive_token,
    hash_token,
    new_token_salt,
    token_matches,
)
from app.domain.tunable import value_for


@dataclass(frozen=True)
class ContactInput:
    """Raw contact fields from the booking form, validated by ``Contact.create``.

    ``answers`` maps the keys of the group's custom form fields to the submitted strings
    (ADR 0055); they are validated by ``validate_answers``.
    """

    first_name: str = field(repr=False)
    last_name: str = field(repr=False)
    email: str = field(repr=False)
    phone: str | None = field(default=None, repr=False)
    answers: Mapping[str, str | None] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class BookingDetails:
    """What the management page needs: the booking, its group and whether it may change.

    ``closure`` is the closure that suspended the booking, for its notice. ``cancellable`` is
    also true for a booking that awaits confirmation ("this was not me", ADR 0056), which
    cannot be rescheduled.
    """

    booking: Booking
    group: Group
    change_deadline: datetime
    changeable: bool
    closure: Closure | None = None
    cancellable: bool = False


@dataclass(frozen=True)
class BookingReceipt:
    """A placed booking and its management token, which exists only here and in the mail."""

    booking: Booking
    management_token: str = field(repr=False)


class BookingService:
    """Application service for citizens' bookings.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        token_key: Server key for deriving management tokens (ADR 0011).
        confirm_policy: The rules for confirming a new booking (ADR 0056), per organisation
            id when it is a function (class D settings, read live).

    Raises:
        ValueError: If ``token_key`` is shorter than 32 bytes.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        token_key: bytes,
        phone_region: Callable[[int], str] = lambda _organisation_id: DEFAULT_PHONE_REGION,
        confirm_policy: ConfirmPolicy | Callable[[int], ConfirmPolicy] = DEFAULT_CONFIRM_POLICY,
    ) -> None:
        """Store the collaborators.

        Args:
            uow: The unit of work.
            clock: The clock.
            token_key: Key for management tokens (ADR 0011).
            phone_region: The organisation's default phone region (class D, ADR 0055).
            confirm_policy: The rules for confirming a new booking (ADR 0056), per
                organisation id when it is a function (class D settings, read live).
        """
        if len(token_key) < MIN_KEY_BYTES:
            raise ValueError(f"the token key must be at least {MIN_KEY_BYTES} bytes")
        self._uow = uow
        self._clock = clock
        self._token_key = token_key
        self._confirm_policy = confirm_policy
        self._phone_region = phone_region

    async def get_group(self, group_slug: str) -> Group:
        """Return the group behind a booking page.

        Raises:
            DomainError: ``group_not_found``.
        """
        async with self._uow.read() as tx:
            return await self._group(tx, group_slug)

    async def closure_notice(self, group: Group) -> Closure | None:
        """Return the closure citizens of ``group`` must see now (active or upcoming)."""
        async with self._uow.read() as tx:
            closures = await tx.get_closures(group.id, self._clock.now())
        return closures[0] if closures else None

    async def closure_image(self, group: Group) -> ImageFile | None:
        """Return the image of the closure citizens of ``group`` see now, if it has one.

        Only a visible (active or upcoming) closure's image is public (ADR 0037 §3).
        """
        async with self._uow.read() as tx:
            closures = await tx.get_closures(group.id, self._clock.now())
            if not closures or closures[0].image is None or closures[0].id is None:
                return None
            return await tx.get_closure_image(closures[0].id)

    async def availability(
        self, group_slug: str, first_day: date, last_day: date
    ) -> list[AvailableSlot]:
        """Return the bookable slots of a group between two local dates (inclusive).

        Raises:
            DomainError: ``group_not_found`` or ``invalid_range``.
        """
        validate_range(first_day, last_day)
        async with self._uow.read() as tx:
            group = await self._group(tx, group_slug)
            rules, overrides = await effective_schedule(tx, group, first_day, last_day)
            lower, upper = utc_bounds(group, first_day, last_day)
            # One slot length beyond the range, for slots of an old grid overlapping its edges.
            margin = timedelta(minutes=group.slot_minutes)
            usage = await tx.get_slot_usage(group.id, lower - margin, upper + margin)
            closures = await tx.get_closures(group.id, self._clock.now())
        slots = available_slots(
            group=group,
            rules=rules,
            overrides=overrides,
            first_day=first_day,
            last_day=last_day,
            now=self._clock.now(),
            booked={u.start: u.active for u in usage},
            snapshots={u.start: u.capacity_snapshot for u in usage},
        )
        return [slot for slot in slots if covering(closures, slot.start) is None]

    async def book(
        self,
        *,
        group_slug: str,
        start: datetime,
        contact: ContactInput,
        locale: str,
        consents: frozenset[ChannelKind] = frozenset(),
    ) -> BookingReceipt:
        """Book the slot at ``start`` for a citizen; it awaits confirmation (ADR 0056).

        Raises:
            DomainError: ``group_not_found``, ``invalid_contact``, ``outside_booking_window``,
                ``slot_unavailable``, ``slot_full`` or ``booking_limit_reached``.
            ValueError: If ``start`` is naive.
        """
        async with self._uow.locked() as tx:
            group = await self._group(tx, group_slug)
            details = await self._contact(tx, group, contact)
            return await self._place(
                tx, group, start, details, locale, consents, None, pending=True
            )

    async def direct_call_availability(self, group_slug: str) -> DirectAvailability:
        """Return whether a direct call can be started now: switched on, open, and room.

        Raises:
            DomainError: ``group_not_found``.
        """
        async with self._uow.read() as tx:
            group = await self._group(tx, group_slug)
            now = self._clock.now()
            if not group.direct_call_enabled or not await self._direct_open(tx, group, now):
                return DirectAvailability.UNAVAILABLE
            if not await self._direct_room(tx, group, now):
                return DirectAvailability.FULL
            return DirectAvailability.AVAILABLE

    async def direct_queue(self, group: Group, booking_id: int | None = None) -> QueueInfo:
        """Return the group's queue of direct calls, with the place of ``booking_id`` (ADR 0026)."""
        now = self._clock.now()
        async with self._uow.read() as tx:
            calls = await tx.get_direct_calls(group.id, now - ESTIMATE_WINDOW)
        return queue_info(calls, group, now, booking_id)

    async def start_direct(
        self, *, group_slug: str, contact: ContactInput, locale: str
    ) -> BookingReceipt:
        """Start a call right away (ADR 0019): a booking in its own slot from now on.

        Raises:
            DomainError: ``group_not_found``, ``invalid_contact``, ``direct_call_unavailable``
                (switched off, or the group is not open now) or ``direct_call_full`` (the
                group's cap of open direct calls is reached).
        """
        async with self._uow.locked() as tx:
            group = await self._group(tx, group_slug)
            now = self._clock.now()
            if not group.direct_call_enabled or not await self._direct_open(tx, group, now):
                raise DomainError(ErrorCode.DIRECT_CALL_UNAVAILABLE)
            if not await self._direct_room(tx, group, now):
                raise DomainError(ErrorCode.DIRECT_CALL_FULL)
            details = await self._contact(tx, group, contact)
            end = now + timedelta(minutes=group.slot_minutes)
            slot = await tx.add_slot(Slot(None, group.id, now, end, 1, BookingKind.DIRECT))
            salt = new_token_salt()
            token = derive_token(self._token_key, salt)
            booking = await tx.add_booking(
                Booking(
                    id=None,
                    group_id=group.id,
                    slot_id=_id(slot),
                    start=now,
                    end=end,
                    status=BookingStatus.CONFIRMED,
                    contact=details,
                    locale=locale,
                    token_hash=hash_token(token),
                    created_at=now,
                    token_salt=salt,
                    kind=BookingKind.DIRECT,
                )
            )
            await tx.replace_jobs(_id(booking), plan_for_direct(start=now, end=end))
        return BookingReceipt(booking, token)

    async def find(self, management_token: str) -> BookingDetails:
        """Return the booking behind a management link, with its group and changeability.

        Raises:
            DomainError: ``booking_not_found``.
        """
        async with self._uow.read() as tx:
            booking = await self._booking(tx, management_token)
            group = await self._group_by_id(tx, booking.group_id)
            closure = (
                await tx.get_closure(booking.closure_id)
                if booking.is_suspended and booking.closure_id is not None
                else None
            )
        deadline = group.change_deadline(booking.start)
        before = self._clock.now() < deadline
        changeable = booking.on_hold or (booking.is_active and before)
        cancellable = changeable or (booking.awaits_confirmation and before)
        return BookingDetails(booking, group, deadline, changeable, closure, cancellable)

    async def confirm(self, management_token: str) -> Booking:
        """Confirm a booking by its email link (ADR 0056) and return it as it now is.

        A pending booking before its ``confirm_by`` is confirmed: the meeting, reminders,
        task and purge are planned as for any booking, with the full confirmation mail. After
        ``confirm_by`` it lapses here, as the worker would. Any other booking is returned
        unchanged, so a second click shows the booking (idempotent).

        Raises:
            DomainError: ``booking_not_found`` or ``group_not_found``.
        """
        async with self._uow.locked() as tx:
            booking = await self._booking(tx, management_token)
            if not booking.awaits_confirmation:
                return booking
            now = self._clock.now()
            if booking.confirm_by is not None and now >= booking.confirm_by:
                await lapse_unconfirmed(tx, booking, now)
                return booking.expire(now)
            group = await self._group_by_id(tx, booking.group_id)
            confirmed = booking.confirm(now)
            await tx.save_booking(confirmed)
            await tx.replace_jobs(_id(booking), await self._booked_jobs(tx, group, confirmed))
            return confirmed

    async def cancel(self, management_token: str) -> Booking:
        """Cancel the booking; its pending jobs become a cancellation mail and the purge.

        Raises:
            DomainError: ``booking_not_found``, ``booking_locked`` (from the change deadline)
                or ``booking_not_active``.
        """
        async with self._uow.locked() as tx:
            booking = await self._booking(tx, management_token)
            now = self._clock.now()
            group = await self._group_by_id(tx, booking.group_id)
            _ensure_changeable(booking, group, now)
            cancelled = booking.cancel(now)
            await tx.save_booking(cancelled)
            jobs = plan_for_cancellation(cancelled_at=now, team_invite=group.sends_team_invite)
            await tx.replace_jobs(_id(booking), jobs)
            return cancelled

    async def reschedule(self, management_token: str, new_start: datetime) -> BookingReceipt:
        """Move a booking to another slot of the same group, keeping contact and locale.

        The old booking is cancelled without a cancellation mail; the new one gets a change
        mail and a new management token. A failure anywhere leaves the old booking untouched.

        Raises:
            DomainError: ``booking_not_found``, ``booking_locked``, ``booking_not_active``
                (also while it awaits confirmation), ``same_slot``, or any error of ``book``
                for the new start.
        """
        async with self._uow.locked() as tx:
            old = await self._booking(tx, management_token)
            if old.awaits_confirmation:  # ADR 0056 §3: confirm first, then move
                raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
            group = await self._group_by_id(tx, old.group_id)
            _ensure_changeable(old, group, self._clock.now())
            return await self.relocate(tx, old, group, new_start)

    async def relocate(
        self,
        tx: Transaction,
        old: Booking,
        group: Group,
        new_start: datetime,
        *,
        sms: bool = False,
    ) -> BookingReceipt:
        """Move ``old`` to ``new_start`` inside the caller's locked transaction.

        Shared by the citizen's reschedule, an admin moving a suspended booking
        (``ClosureService.move``) and a planner (``PlannerService.move``, which passes
        ``sms`` so the change also goes out as an SMS where the citizen agreed to it); the
        caller checks who may do it.

        Raises:
            DomainError: ``booking_not_active``, ``same_slot`` or any error of ``book``.
        """
        if not (old.holds_place or old.awaits_rebook) or old.contact is None:
            raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
        if new_start == old.start:
            raise DomainError(ErrorCode.SAME_SLOT)
        now = self._clock.now()
        await tx.save_booking(old.cancel(now))
        jobs = plan_for_cancellation(
            cancelled_at=now, notify=False, team_invite=group.sends_team_invite
        )
        await tx.replace_jobs(_id(old), jobs)
        return await self._place(
            tx,
            group,
            new_start,
            old.contact,
            old.locale,
            old.consents,
            _id(old),
            sms=sms,
            pending=old.awaits_confirmation,
            confirmed_at=old.confirmed_at,
        )

    async def _direct_open(self, tx: Transaction, group: Group, now: datetime) -> bool:
        """Return whether the group is open for a direct call at ``now``.

        Holidays count as closed days (ADR 0025), and so does an active emergency closure.
        """
        today = group.today(now)
        rules, overrides = await effective_schedule(tx, group, today, today)
        if any(c.is_active(now) for c in await tx.get_closures(group.id, now)):
            return False
        return is_open(group, rules, overrides, now)

    async def _direct_room(self, tx: Transaction, group: Group, now: datetime) -> bool:
        """Return whether another direct call fits under the group's cap at ``now``."""
        if group.direct_call_max_concurrent is None:
            return True
        return has_room(group, await tx.get_direct_calls(group.id, load_since(group, now)), now)

    async def get_form(self, group: Group) -> FormDefinition:
        """Return the custom fields of ``group``'s booking form (ADR 0055)."""
        async with self._uow.read() as tx:
            return await tx.get_form(group.id)

    async def _contact(self, tx: Transaction, group: Group, contact: ContactInput) -> Contact:
        """Validate the contact details and the answers to the group's custom fields.

        Raises:
            DomainError: ``invalid_contact`` or ``invalid_answer`` naming the field.
        """
        region = self._phone_region(group.organisation_id)
        answers = validate_answers(
            await tx.get_form(group.id),
            contact.answers,
            today=self._clock.now().astimezone(group.tz).date(),
            region=region,
        )
        return Contact.create(
            first_name=contact.first_name,
            last_name=contact.last_name,
            email=contact.email,
            phone=contact.phone,
            phone_mode=group.phone_mode,
            region=region,
            answers=answers if answers.visible(Purpose.PLANNER_VIEW) else None,
        )

    async def _group(self, tx: Transaction, slug: str) -> Group:
        """Return the group or raise ``group_not_found``."""
        group = await tx.get_group(slug)
        if group is None:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        return group

    async def _group_by_id(self, tx: Transaction, group_id: int) -> Group:
        """Return the booking's group or raise ``group_not_found``."""
        group = await tx.get_group_by_id(group_id)
        if group is None:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        return group

    async def _booking(self, tx: Transaction, management_token: str) -> Booking:
        """Return the booking for a token or raise ``booking_not_found``."""
        booking = await tx.get_booking_by_token_hash(hash_token(management_token))
        if booking is None or not token_matches(management_token, booking.token_hash):
            raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
        return booking

    async def _place(
        self,
        tx: Transaction,
        group: Group,
        start: datetime,
        contact: Contact,
        locale: str,
        consents: frozenset[ChannelKind],
        rescheduled_from: int | None,
        *,
        sms: bool = False,
        pending: bool = False,
        confirmed_at: datetime | None = None,
    ) -> BookingReceipt:
        """Reserve a place at ``start`` and store the booking with its jobs.

        ``pending`` places it awaiting confirmation with its own ``confirm_by`` (a citizen's
        new booking, or a moved one that was still pending, ADR 0056); otherwise it is placed
        confirmed and keeps ``confirmed_at`` of the booking it replaces.
        """
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        now = self._clock.now()
        earliest, latest = booking_window(group, now)
        if not earliest <= start <= latest:
            raise DomainError(ErrorCode.OUTSIDE_BOOKING_WINDOW)
        day = start.astimezone(group.tz).date()
        rules, overrides = await effective_schedule(tx, group, day, day)
        offered = offered_slot(group, rules, overrides, start)
        if offered is None:
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        if covering(await tx.get_closures(group.id, now), offered.start) is not None:
            raise DomainError(ErrorCode.GROUP_CLOSED)
        slot = await tx.get_slot(group.id, offered.start)
        if slot is None:
            slot = await tx.add_slot(
                Slot(None, group.id, offered.start, offered.end, offered.capacity)
            )
        length = offered.end - offered.start
        usage = await tx.get_slot_usage(group.id, offered.start - length, offered.start + length)
        overlap = overlapping_active(offered.start, length, {u.start: u.active for u in usage})
        if await tx.count_active_bookings(_id(slot)) + overlap >= slot.capacity_snapshot:
            raise DomainError(ErrorCode.SLOT_FULL)
        await _check_address_limit(tx, group, contact, slot.start, now)
        window = (
            confirm_window(
                now=now,
                start=slot.start,
                dispatch_lead=group.dispatch_lead,
                policy=value_for(self._confirm_policy, group.organisation_id),
            )
            if pending
            else None
        )
        salt = new_token_salt()
        token = derive_token(self._token_key, salt)
        booking = await tx.add_booking(
            Booking(
                id=None,
                group_id=group.id,
                slot_id=_id(slot),
                start=slot.start,
                end=slot.end,
                status=BookingStatus.PENDING_CONFIRMATION if window else BookingStatus.CONFIRMED,
                contact=contact,
                locale=locale,
                token_hash=hash_token(token),
                created_at=now,
                token_salt=salt,
                consents=consents,
                rescheduled_from_id=rescheduled_from,
                confirm_by=window.confirm_by if window else None,
                confirmed_at=confirmed_at,
            )
        )
        jobs = (
            plan_for_pending(now=now, window=window)
            if window
            else await self._booked_jobs(tx, group, booking, sms=sms)
        )
        await tx.replace_jobs(_id(booking), jobs)
        return BookingReceipt(booking, token)

    async def _booked_jobs(
        self, tx: Transaction, group: Group, booking: Booking, *, sms: bool = False
    ) -> list[PlannedJob]:
        """Return the jobs of a booking that goes ahead: meeting, mails, reminders, task, purge.

        The first mail is the change mail for a booking that replaces another, else the
        confirmation.
        """
        if booking.contact is None:
            raise ValueError("booking has no contact details")
        return plan_for_booking(
            start=booking.start,
            end=booking.end,
            contact=booking.contact,
            consents=booking.consents,
            rules=await tx.get_reminder_rules(group.id),
            now=self._clock.now(),
            dispatch_lead=group.dispatch_lead,
            message=JobType.SEND_CHANGE
            if booking.rescheduled_from_id
            else JobType.SEND_CONFIRMATION,
            sms=sms,
            team_invite=group.sends_team_invite,
        )


async def _check_address_limit(
    tx: Transaction, group: Group, contact: Contact, start: datetime, now: datetime
) -> None:
    """Refuse a second place for one address in a slot, or more than the group allows.

    Runs after the capacity check, so a full slot is reported as full to everyone. The
    refusal does not say which rule applied; the page must not reveal that an address
    already has a booking (ADR 0066).

    Raises:
        DomainError: ``booking_limit_reached``.
    """
    starts = await tx.upcoming_starts_for_email(group.id, contact.email, now)
    if start in starts or len(starts) >= group.max_bookings_per_email:
        raise DomainError(ErrorCode.BOOKING_LIMIT_REACHED)


def _ensure_changeable(booking: Booking, group: Group, now: datetime) -> None:
    """Refuse changes from the change deadline on; the booking is read-only from then.

    A booking on hold (suspended, or awaiting a new time) has no deadline: it does not go
    ahead at its time anyway.
    """
    if booking.on_hold:
        return
    if now >= group.change_deadline(booking.start):
        raise DomainError(ErrorCode.BOOKING_LOCKED)


def _id(entity: Booking | Slot) -> int:
    """Return the ID of a stored entity; a missing ID is a programming error."""
    if entity.id is None:
        raise ValueError("entity has not been stored")
    return entity.id
