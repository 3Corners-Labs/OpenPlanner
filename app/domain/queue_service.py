"""Application service of the own queue (ADR 0043): view, pick up, claim, release.

Every use case authorises ``queue.work`` on the group (ADR 0014) and works only on bookings
of that group; a booking of another group does not exist here. A claim runs in a locked
transaction (``BEGIN IMMEDIATE``) with a conditional update, so of two agents claiming at once
exactly one wins; the other gets ``already_claimed`` (a chosen row) or the next row.

    view        ─► visible rows, first names only, which one is next
    summaries   ─► per own queue in reach: waiting, longest wait, staff signed in (ADR 0061)
    claim_next  ─► locked: next unclaimed ─► claim_booking ─► audit queue.claimed
    claim       ─► only with queue_free_pick_allowed
    claimed     ─► the meeting link, for the claiming agent only
    release     ─► the claiming agent, or anyone who may view the group's bookings

The meeting link is shown only after a claim, so two agents cannot both start the same
meeting unannounced. The agent is recorded as a pseudonym (``tokens.agent_pseudonym``).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal, Scope, Target, decide, scope_for
from app.domain.booking import Booking, Claim
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import (
    FALLBACK_LOCALE,
    FormDefinition,
    LabelledAnswer,
    Purpose,
    labelled_answers,
)
from app.domain.own_queue import (
    QueueEntry,
    next_up,
    not_claimed_in_time,
    summarise,
    visible,
    works_queue,
)
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, GroupSummary
from app.domain.staff import StaffFilter, StaffMember
from app.domain.tokens import agent_pseudonym

CLAIMED = "queue.claimed"
RELEASED = "queue.released"
"""Audit details of the two changes (``action`` is the permission, ``queue.work``)."""


@dataclass(frozen=True)
class QueueRow:
    """One booking in the queue as an agent sees it: never email, phone or note.

    Attributes:
        booking_id: The booking.
        reference: Its reference, e.g. ``OP-000123``.
        start: Its start (UTC); shown in the group's time zone.
        first_name: The citizen's first name; empty after the purge.
        in_lobby: The citizen joined and waits for an agent.
        claimed: An agent picked it up.
        mine: The viewing agent picked it up.
        direct: A direct call (ADR 0019) rather than a scheduled appointment.
        overdue: Not claimed ``not_picked_up_minutes`` after its start.
    """

    booking_id: int
    reference: str
    start: datetime
    first_name: str = field(repr=False)
    in_lobby: bool
    claimed: bool
    mine: bool
    direct: bool
    overdue: bool


@dataclass(frozen=True)
class QueueView:
    """The own queue of one group at one moment."""

    group: Group
    rows: list[QueueRow]
    next_booking_id: int | None
    free_pick: bool
    can_release_any: bool


@dataclass(frozen=True)
class GroupQueueSummary:
    """One own queue as the overview shows it: counts and a duration, no booking fields.

    Attributes:
        summary: The group with its organisation.
        waiting: Citizens in the lobby nobody picked up yet.
        longest_wait: How long the first of them has waited.
        staff_available: Staff who may work this queue and are signed in now.
    """

    summary: GroupSummary
    waiting: int
    longest_wait: timedelta
    staff_available: int


@dataclass(frozen=True)
class ClaimedBooking:
    """A booking the agent claimed, with the link to join its meeting."""

    booking_id: int
    reference: str
    start: datetime
    first_name: str = field(repr=False)
    join_url: str | None = field(repr=False)
    answers: tuple[LabelledAnswer, ...] = field(default=(), repr=False)
    """Answers to the group's custom fields an agent may see: never PII (ADR 0055)."""


class QueueService:
    """Lets agents work a group's own queue.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises every use case and audits refusals.
        pseudonym_key: Key of the agents' pseudonyms (``keys.agent_pseudonym_key``).
    """

    def __init__(
        self, uow: UnitOfWork, clock: Clock, admin: AdminService, pseudonym_key: bytes
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._key = pseudonym_key

    async def view(self, principal: Principal, slug: str) -> QueueView:
        """Return the group's queue as ``principal`` sees it.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``queue_not_enabled``.
        """
        summary = await self._group(principal, slug)
        group = summary.group
        now = self._clock.now()
        async with self._uow.read() as tx:
            entries = await self._entries(tx, principal, group, now)
        me = agent_pseudonym(self._key, principal.subject)
        limit = timedelta(minutes=group.not_picked_up_minutes)
        nxt = next_up(entries)
        return QueueView(
            group,
            [_row(e, me, not_claimed_in_time(e, now, limit)) for e in entries],
            nxt.booking.id if nxt else None,
            group.queue_free_pick_allowed,
            _supervises(principal, summary),
        )

    async def summaries(
        self, principal: Principal, session_lifetime: timedelta
    ) -> list[GroupQueueSummary]:
        """Return every own queue in ``principal``'s ``queue.work`` reach, busiest first.

        Counts only (ADR 0061 §8). Staff available are the directory's members (ADR 0046)
        whose last login grants ``queue.work`` on the group and whose session can still be
        open (``session_lifetime``, the absolute lifetime). Superusers are not in an
        organisation's directory and do not count.
        """
        scope = scope_for(principal, Permission.QUEUE_WORK)
        now = self._clock.now()
        async with self._uow.read() as tx:
            groups = [g for g in await tx.list_groups(scope) if g.group.has_own_queue]
            staff = {
                organisation: await tx.list_staff(
                    Scope(organisations=frozenset({organisation})),
                    StaffFilter(organisation=organisation),
                )
                for organisation in {g.organisation_slug for g in groups}
            }
            rows = []
            for summary in groups:
                queue = summarise(await self._entries(tx, principal, summary.group, now), now)
                available = _signed_in_for(
                    staff[summary.organisation_slug], summary, now, session_lifetime
                )
                rows.append(
                    GroupQueueSummary(summary, queue.waiting, queue.longest_wait, available)
                )
        return sorted(rows, key=lambda r: (-r.waiting, -r.longest_wait, r.summary.group.name))

    async def claim_next(self, principal: Principal, slug: str) -> ClaimedBooking:
        """Claim the booking "pick up next" points at.

        Raises:
            DomainError: ``queue_empty`` when nothing is left to claim, or as ``view``.
        """
        summary = await self._group(principal, slug)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            nxt = next_up(await self._entries(tx, principal, summary.group, now))
            if nxt is None:
                raise DomainError(ErrorCode.QUEUE_EMPTY)
            return await self._claim(tx, principal, summary, nxt.booking, now)

    async def claim(self, principal: Principal, slug: str, booking_id: int) -> ClaimedBooking:
        """Claim one chosen booking; only where the group allows free picking.

        Raises:
            DomainError: ``free_pick_not_allowed``, ``booking_not_found``,
                ``already_claimed``, ``booking_not_active``, or as ``view``.
        """
        summary = await self._group(principal, slug)
        if not summary.group.queue_free_pick_allowed:
            raise DomainError(ErrorCode.FREE_PICK_NOT_ALLOWED)
        async with self._uow.locked() as tx:
            booking = await _booking_of(tx, summary.group, booking_id)
            if booking.claim is not None:
                raise DomainError(ErrorCode.ALREADY_CLAIMED)
            return await self._claim(tx, principal, summary, booking, self._clock.now())

    async def claimed(
        self, principal: Principal, slug: str, booking_id: int, locale: str = FALLBACK_LOCALE
    ) -> ClaimedBooking:
        """Return a booking ``principal`` claimed, with its meeting link.

        The answers an agent may see (never PII) come with the labels in ``locale``.

        Raises:
            DomainError: ``not_claimed`` unless ``principal`` holds the claim, or as ``claim``.
        """
        group = (await self._group(principal, slug)).group
        async with self._uow.read() as tx:
            booking = await _booking_of(tx, group, booking_id)
            form = await tx.get_form(group.id)
        if not self._holds(principal, booking):
            raise DomainError(ErrorCode.NOT_CLAIMED)
        return _claimed(booking, form, locale)

    async def release(self, principal: Principal, slug: str, booking_id: int) -> None:
        """Put a claimed booking back at the top of the queue.

        The claiming agent may release it, and so may anyone who may view the group's
        bookings (group admins), for example when an agent left without releasing.

        Raises:
            DomainError: ``not_claimed`` for anyone else or an unclaimed booking.
        """
        summary = await self._group(principal, slug)
        supervisor = _supervises(principal, summary)
        async with self._uow.locked() as tx:
            booking = await _booking_of(tx, summary.group, booking_id)
            mine = self._holds(principal, booking)
            if booking.claim is None or not (mine or supervisor):
                raise DomainError(ErrorCode.NOT_CLAIMED)
            await tx.release_claim(booking_id)
            await self._audit(tx, principal, summary, booking_id, RELEASED)

    async def _group(self, principal: Principal, slug: str) -> GroupSummary:
        """Authorise ``queue.work`` on the group and return it if it has an own queue."""
        authorization = require(
            await self._admin.authorize(principal, Permission.QUEUE_WORK, group_slug=slug)
        )
        if authorization.group is None:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        if not authorization.group.group.has_own_queue:
            raise DomainError(ErrorCode.QUEUE_NOT_ENABLED)
        return authorization.group

    async def _entries(
        self, tx: Transaction, principal: Principal, group: Group, now: datetime
    ) -> list[QueueEntry]:
        """Load the bookings around ``now`` with their call status and keep the visible ones.

        A booking ends one slot length after its start, so earlier ones have ended.
        """
        lower = now - timedelta(minutes=group.slot_minutes)
        upper = now + timedelta(minutes=group.queue_visible_from_minutes, seconds=1)
        scope = scope_for(principal, Permission.QUEUE_WORK)
        overviews = await tx.list_bookings(scope, group.id, lower, upper)
        return visible((QueueEntry(o.booking, o.status) for o in overviews), group, now)

    async def _claim(
        self,
        tx: Transaction,
        principal: Principal,
        summary: GroupSummary,
        booking: Booking,
        now: datetime,
    ) -> ClaimedBooking:
        """Record the claim on ``booking`` and audit it, in the caller's locked transaction."""
        booking_id = _id(booking)
        claim = Claim(now, agent_pseudonym(self._key, principal.subject))
        if not await tx.claim_booking(booking_id, claim):
            raise DomainError(ErrorCode.ALREADY_CLAIMED)
        await self._audit(tx, principal, summary, booking_id, CLAIMED)
        return _claimed(booking)

    def _holds(self, principal: Principal, booking: Booking) -> bool:
        """Return whether ``principal`` holds the claim on ``booking``."""
        return booking.claim is not None and booking.claim.claimed_by == agent_pseudonym(
            self._key, principal.subject
        )

    async def _audit(
        self,
        tx: Transaction,
        principal: Principal,
        summary: GroupSummary,
        booking_id: int,
        detail: str,
    ) -> None:
        """Record a claim or release in the same transaction, with the booking id."""
        await tx.add_audit_event(
            AuditEvent(
                self._clock.now(),
                principal.subject,
                Permission.QUEUE_WORK.value,
                Outcome.SUCCESS,
                organisation=summary.organisation_slug,
                group=summary.group.slug,
                detail=detail,
                booking_id=booking_id,
            )
        )


def _signed_in_for(
    members: list[StaffMember], summary: GroupSummary, now: datetime, lifetime: timedelta
) -> int:
    """Count the members signed in now who may work the group's queue."""
    return sum(
        1
        for m in members
        if m.is_signed_in(now, lifetime)
        and works_queue(m, summary.group.slug, summary.organisation_slug)
    )


def _supervises(principal: Principal, summary: GroupSummary) -> bool:
    """Return whether ``principal`` may release any claim: who may view the group's bookings.

    Not audited as a denial: holding the claim is enough for the agent who claimed it.
    """
    target = Target.for_group(summary.group.slug, summary.organisation_slug)
    return decide(principal, Permission.BOOKINGS_VIEW, target).allowed


def _row(entry: QueueEntry, me: str, overdue: bool) -> QueueRow:
    """Map a queue entry to what the agent sees."""
    booking = entry.booking
    claim = booking.claim
    return QueueRow(
        booking_id=_id(booking),
        reference=booking.reference,
        start=booking.start,
        first_name=booking.contact.first_name if booking.contact else "",
        in_lobby=entry.in_lobby,
        claimed=claim is not None,
        mine=claim is not None and claim.claimed_by == me,
        direct=entry.is_direct,
        overdue=overdue,
    )


def _claimed(
    booking: Booking, form: FormDefinition | None = None, locale: str = FALLBACK_LOCALE
) -> ClaimedBooking:
    """Return what the claiming agent sees of ``booking``: first name and non-PII answers."""
    contact = booking.contact
    return ClaimedBooking(
        booking_id=_id(booking),
        reference=booking.reference,
        start=booking.start,
        first_name=contact.first_name if contact else "",
        join_url=booking.meeting.link if booking.meeting else None,
        answers=labelled_answers(
            contact.answers if contact else None,
            form or FormDefinition(1),
            Purpose.AGENT_VIEW,
            locale,
        ),
    )


async def _booking_of(tx: Transaction, group: Group, booking_id: int) -> Booking:
    """Return the group's booking ``booking_id``; another group's booking does not exist."""
    booking = await tx.get_booking(booking_id)
    if booking is None or booking.group_id != group.id:
        raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
    if not booking.is_active:
        raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
    return booking


def _id(booking: Booking) -> int:
    """Return the ID of a stored booking; a missing ID is a programming error."""
    if booking.id is None:
        raise ValueError("booking without id")
    return booking.id
