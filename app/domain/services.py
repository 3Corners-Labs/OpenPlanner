"""Booking use cases: availability, book, find, cancel, reschedule.

Every write runs in one locked transaction, so slot creation, the capacity check, the
booking and its jobs commit together or not at all (CLAUDE.md architecture rules).

    book ─► locked tx ─► _place: window ─► offered? ─► slot (create, snapshot capacity)
                                ─► capacity ─► booking + token ─► replace_jobs
    reschedule ─► locked tx ─► cancel old (jobs: purge only) ─► _place new in the SAME group

A booking is read-only from its change deadline (``job_plan.change_deadline``: the group's
dispatch lead before the start, T−5 min by default): cancel and reschedule then raise
``booking_locked``.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.availability import (
    AvailableSlot,
    available_slots,
    booking_window,
    offered_slot,
    utc_bounds,
    validate_range,
)
from app.domain.booking import Booking, BookingStatus, Contact, Slot
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import change_deadline, plan_for_booking, plan_for_cancellation
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group
from app.domain.tokens import hash_token, new_management_token, token_matches


@dataclass(frozen=True)
class ContactInput:
    """Raw contact fields from the booking form, validated by ``Contact.create``."""

    name: str = field(repr=False)
    email: str = field(repr=False)
    phone: str | None = field(default=None, repr=False)


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
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock

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
            rules, overrides = await tx.get_schedule(group.id, first_day, last_day)
            usage = await tx.get_slot_usage(group.id, *utc_bounds(group, first_day, last_day))
        return available_slots(
            group=group,
            rules=rules,
            overrides=overrides,
            first_day=first_day,
            last_day=last_day,
            now=self._clock.now(),
            booked={u.start: u.active for u in usage},
            snapshots={u.start: u.capacity_snapshot for u in usage},
        )

    async def book(
        self,
        *,
        group_slug: str,
        start: datetime,
        contact: ContactInput,
        locale: str,
        consents: frozenset[ChannelKind] = frozenset(),
    ) -> BookingReceipt:
        """Book the slot at ``start`` for a citizen.

        Raises:
            DomainError: ``group_not_found``, ``invalid_contact``, ``outside_booking_window``,
                ``slot_unavailable`` or ``slot_full``.
            ValueError: If ``start`` is naive.
        """
        async with self._uow.locked() as tx:
            group = await self._group(tx, group_slug)
            details = Contact.create(
                name=contact.name,
                email=contact.email,
                phone=contact.phone,
                phone_mode=group.phone_mode,
            )
            return await self._place(tx, group, start, details, locale, consents, None)

    async def find(self, management_token: str) -> Booking:
        """Return the booking behind a management link.

        Raises:
            DomainError: ``booking_not_found``.
        """
        async with self._uow.read() as tx:
            return await self._booking(tx, management_token)

    async def cancel(self, management_token: str) -> Booking:
        """Cancel the booking; its pending jobs become a cancellation mail and the purge.

        Raises:
            DomainError: ``booking_not_found``, ``booking_locked`` (from the change deadline)
                or ``booking_not_active``.
        """
        async with self._uow.locked() as tx:
            booking = await self._booking(tx, management_token)
            now = self._clock.now()
            _ensure_changeable(booking, await self._group_by_id(tx, booking.group_id), now)
            cancelled = booking.cancel(now)
            await tx.save_booking(cancelled)
            await tx.replace_jobs(_id(booking), plan_for_cancellation(cancelled_at=now))
            return cancelled

    async def reschedule(self, management_token: str, new_start: datetime) -> BookingReceipt:
        """Move a booking to another slot of the same group, keeping contact and locale.

        The old booking is cancelled without a cancellation mail; the new one gets a change
        mail and a new management token. A failure anywhere leaves the old booking untouched.

        Raises:
            DomainError: ``booking_not_found``, ``booking_locked``, ``booking_not_active``,
                ``same_slot``, or any error of ``book`` for the new start.
        """
        async with self._uow.locked() as tx:
            old = await self._booking(tx, management_token)
            now = self._clock.now()
            group = await self._group_by_id(tx, old.group_id)
            _ensure_changeable(old, group, now)
            if not old.is_active or old.contact is None:
                raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
            if new_start == old.start:
                raise DomainError(ErrorCode.SAME_SLOT)
            await tx.save_booking(old.cancel(now))
            await tx.replace_jobs(_id(old), plan_for_cancellation(cancelled_at=now, notify=False))
            return await self._place(
                tx, group, new_start, old.contact, old.locale, old.consents, _id(old)
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
    ) -> BookingReceipt:
        """Reserve a place at ``start`` and store the booking with its jobs."""
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        now = self._clock.now()
        earliest, latest = booking_window(group, now)
        if not earliest <= start <= latest:
            raise DomainError(ErrorCode.OUTSIDE_BOOKING_WINDOW)
        day = start.astimezone(group.tz).date()
        rules, overrides = await tx.get_schedule(group.id, day, day)
        offered = offered_slot(group, rules, overrides, start)
        if offered is None:
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        slot = await tx.get_slot(group.id, offered.start)
        if slot is None:
            slot = await tx.add_slot(
                Slot(None, group.id, offered.start, offered.end, offered.capacity)
            )
        if await tx.count_active_bookings(_id(slot)) >= slot.capacity_snapshot:
            raise DomainError(ErrorCode.SLOT_FULL)
        token, token_hash = new_management_token()
        booking = await tx.add_booking(
            Booking(
                id=None,
                group_id=group.id,
                slot_id=_id(slot),
                start=slot.start,
                end=slot.end,
                status=BookingStatus.CONFIRMED,
                contact=contact,
                locale=locale,
                token_hash=token_hash,
                created_at=now,
                consents=consents,
                rescheduled_from_id=rescheduled_from,
            )
        )
        jobs = plan_for_booking(
            start=slot.start,
            end=slot.end,
            contact=contact,
            consents=consents,
            rules=await tx.get_reminder_rules(group.id),
            now=now,
            dispatch_lead=group.dispatch_lead,
            rescheduled=rescheduled_from is not None,
        )
        await tx.replace_jobs(_id(booking), jobs)
        return BookingReceipt(booking, token)


def _ensure_changeable(booking: Booking, group: Group, now: datetime) -> None:
    """Refuse changes from the change deadline on; the booking is read-only from then."""
    if now >= change_deadline(booking.start, group.dispatch_lead):
        raise DomainError(ErrorCode.BOOKING_LOCKED)


def _id(entity: Booking | Slot) -> int:
    """Return the ID of a stored entity; a missing ID is a programming error."""
    if entity.id is None:
        raise ValueError("entity has not been stored")
    return entity.id
