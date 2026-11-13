"""Application service of the own queue (ADR 0043): view, pick up, claim, release.

Every use case authorises ``queue.work`` on the group (ADR 0014) and works only on bookings
of that group; a booking of another group does not exist here. A claim runs in a locked
transaction (``BEGIN IMMEDIATE``) with a conditional update, so of two agents claiming at once
exactly one wins; the other gets ``already_claimed`` (a chosen row) or the next row.

    view        ─► visible rows, first names only, which one is next for the viewer
    summaries   ─► per own queue in reach: waiting, longest wait, staff signed in (ADR 0061)
    claim_next  ─► locked: next unclaimed the agent is eligible for ─► claim_booking ─► audit
    claim       ─► only with queue_free_pick_allowed; locked: eligible ─► claim_booking
    claimed     ─► eligible again (the start of treatment) ─► the meeting link, claimer only
    release     ─► the claiming agent, or anyone who may view the group's bookings
    start       ─► request work only: holder, eligible, lease, the pool's check_start (locked)
    complete    ─► request work only: holder (or a supervisor in recovery) ─► unit released

Request work (ADR 0121 step 5): a booking of a flow without video on a counted pool. Its claim
also makes the request and a work item with a lease (``RequestWork``); its page offers *Start*
and *Complete* instead of a meeting link; releasing it ends the unstarted work item, and started
work is completed, never released. Every other booking is the queue's work as before.

Skills (ADR 0121 point 5): a claim needs the right (``queue.work``), the skills the booking's
flow revision and subject require (``WorkEligibility``, read inside the locked transaction) and
an assignable booking. Work an agent may not take stays visible in its place with the missing
skills named (choice S3); work without skills, every legacy flow among it, is open as before.

The meeting link is shown only after a claim, so two agents cannot both start the same
meeting unannounced. The agent is recorded as a pseudonym (``tokens.agent_pseudonym``).
Every row and the claimed booking carry the video link's state and the participant's state
(ADR 0072), so the page can say what is going on instead of showing "no meeting".
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from app.domain.admin import AdminService, record_change
from app.domain.authorization import Permission, Principal, Scope, Target, decide, scope_for
from app.domain.booking import Booking, Claim
from app.domain.calls import CallStatus, new_status
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import (
    FALLBACK_LOCALE,
    FormDefinition,
    LabelledAnswer,
    Purpose,
    labelled_answers,
)
from app.domain.lookups import group_booking
from app.domain.own_queue import (
    ParticipantState,
    QueueEntry,
    VideoState,
    next_up,
    participant_state,
    summarise,
    video_state,
    visible,
    works_queue,
)
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.request_work import RequestWork, Work, recovering
from app.domain.routing import RoutingChoice
from app.domain.scheduling import Group, GroupSummary
from app.domain.skills import Missing, Skill
from app.domain.staff import StaffFilter, StaffMember
from app.domain.staff_usage import StaffUsage
from app.domain.tokens import agent_pseudonym, agent_pseudonyms
from app.domain.usage_recording import record_queue_claimed, record_queue_released
from app.domain.work_eligibility import WorkEligibility

CLAIMED = "queue.claimed"
RELEASED = "queue.released"
STARTED = "queue.started"
STARTED_IN_CONFLICT = "queue.started_in_conflict"
COMPLETED = "queue.completed"
"""Audit details of the changes (``action`` is the permission, ``queue.work``); start and
completion are of request work only (ADR 0121 step 5)."""


@dataclass(frozen=True)
class QueueRow:
    """One booking in the queue as an agent sees it: never email, phone or note.

    Attributes:
        booking_id: The booking.
        reference: Its reference, e.g. ``OP-000123``.
        start: Its start (UTC); shown in the group's time zone.
        first_name: The participant's first name; empty after the purge.
        in_lobby: The participant joined and waits for an agent.
        claimed: An agent picked it up.
        mine: The viewing agent picked it up.
        direct: A direct call (ADR 0019) rather than a scheduled appointment.
        overdue: Not claimed ``not_picked_up_minutes`` after its start.
        video: Whether the meeting is ready, pending or failed (ADR 0072).
        routing: The subject and attribute codes the participant chose (ADR 0081 point 4).
        fallback: The booking fell back to this queue from the contact center (ADR 0082).
        eligible: The viewer holds every skill the booking requires (ADR 0121 point 5).
        missing: The skills the booking requires that the viewer does not hold, to name them
            (choice S3); empty when the viewer may take it.
        without_video: Request work of a flow without video (ADR 0121 step 5).
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
    video: VideoState = VideoState.PENDING
    routing: RoutingChoice | None = None
    fallback: bool = False
    eligible: bool = True
    missing: tuple[Skill, ...] = ()
    without_video: bool = False


@dataclass(frozen=True)
class QueueView:
    """The own queue of one group at one moment."""

    group: Group
    rows: list[QueueRow]
    next_booking_id: int | None
    """The booking "pick up next" claims for the viewer: the first open one they may take."""
    free_pick: bool
    can_release_any: bool


@dataclass(frozen=True)
class GroupQueueSummary:
    """One own queue as the overview shows it: counts and a duration, no booking fields.

    Attributes:
        summary: The group with its organisation.
        waiting: Participants nobody picked up yet: in the lobby, or direct callers since their
            request.
        longest_wait: How long the first of them has waited.
        staff_available: Staff who may work this queue and are signed in now.
        not_picked_up: Bookings nobody claimed ``not_picked_up_minutes`` after their start
            (ADR 0043 point 8).
    """

    summary: GroupSummary
    waiting: int
    longest_wait: timedelta
    staff_available: int
    not_picked_up: int = 0


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
    video: VideoState = VideoState.PENDING
    """Whether the meeting is ready, pending or failed (ADR 0072)."""
    participant: ParticipantState = ParticipantState.NOT_YET
    """Where the participant is: not yet joined, in the lobby, in the call, or gone."""
    work: Work | None = None
    """The request and its work item for request work (ADR 0121 step 5), else ``None``."""


class QueueService:
    """Lets agents work a group's own queue.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises every use case and audits refusals.
        pseudonym_key: Key of the agents' pseudonyms (``keys.agent_pseudonym_key``).
        previous_pseudonym_key: The same key under ``MANAGEMENT_TOKEN_KEY_PREVIOUS`` during a
            rotation (ADR 0094 point 4): earlier claims still read as the agent's own.
        staff_usage: Writes the claim as a fact about the agent while the organisation has the
            team view on (ADR 0117 point 9); ``None`` writes none.
        work: The request work of flows without video on a counted pool (ADR 0121 step 5);
            ``None`` treats every booking as before.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        pseudonym_key: bytes,
        previous_pseudonym_key: bytes | None = None,
        staff_usage: StaffUsage | None = None,
        work: RequestWork | None = None,
    ) -> None:
        """Store the collaborators; ``staff_usage`` adds the claim to the team member's facts."""
        self._work = work
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._key = pseudonym_key
        self._keys = (pseudonym_key, *([previous_pseudonym_key] if previous_pseudonym_key else []))
        self._staff_usage = staff_usage

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
            judge = WorkEligibility(tx, principal, summary.organisation_slug)
            lacking = await _lacking(judge, entries)
            named = {i: await judge.skills(ids) for i, ids in lacking.items() if ids}
            requests = {
                _id(e.booking)
                for e in entries
                if self._work and await self._work.handles(tx, e.booking)
            }
        me = agent_pseudonyms(self._keys, principal.subject)
        limit = timedelta(minutes=group.not_picked_up_minutes)
        nxt = next_up(e for e in entries if not lacking[_id(e.booking)])
        rows = [
            replace(
                _row(e, me, e.not_picked_up(now, limit)),
                eligible=not lacking[_id(e.booking)],
                missing=named.get(_id(e.booking), ()),
                without_video=_id(e.booking) in requests,
            )
            for e in entries
        ]
        return QueueView(
            group,
            rows,
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
            groups = [g for g in await tx.list_groups(scope) if g.group.queue_open]
            staff = {
                organisation: await tx.list_staff(
                    Scope(organisations=frozenset({organisation})),
                    StaffFilter(organisation=organisation),
                )
                for organisation in {g.organisation_slug for g in groups}
            }
            rows = []
            for summary in groups:
                entries = await self._entries(tx, principal, summary.group, now)
                queue = summarise(entries, now)
                limit = timedelta(minutes=summary.group.not_picked_up_minutes)
                available = _signed_in_for(
                    staff[summary.organisation_slug], summary, now, session_lifetime
                )
                rows.append(
                    GroupQueueSummary(
                        summary,
                        queue.waiting,
                        queue.longest_wait,
                        available,
                        sum(1 for e in entries if e.not_picked_up(now, limit)),
                    )
                )
        return sorted(rows, key=lambda r: (-r.waiting, -r.longest_wait, r.summary.group.name))

    async def claim_next(self, principal: Principal, slug: str) -> ClaimedBooking:
        """Claim the booking "pick up next" points at: the first open one the agent may take.

        Eligibility is read inside the locked transaction, so a skill revoked after the page was
        shown counts (A09).

        Raises:
            DomainError: ``queue_empty`` when nothing is left to claim, ``not_eligible`` when
                open work is left but none of it fits the agent's skills, or as ``view``.
        """
        summary = await self._group(principal, slug)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            entries = await self._entries(tx, principal, summary.group, now)
            judge = WorkEligibility(tx, principal, summary.organisation_slug)
            nxt = next_up(entries)
            if nxt is None:
                raise DomainError(ErrorCode.QUEUE_EMPTY)
            if isinstance(await judge.of(nxt.booking), Missing):
                lacking = await _lacking(judge, entries)
                nxt = next_up(e for e in entries if not lacking[_id(e.booking)])
                if nxt is None:
                    raise DomainError(ErrorCode.NOT_ELIGIBLE)
            return await self._claim(tx, principal, summary, nxt.booking, now)

    async def claim(self, principal: Principal, slug: str, booking_id: int) -> ClaimedBooking:
        """Claim one chosen booking; only where the group allows free picking.

        Raises:
            DomainError: ``free_pick_not_allowed``, ``booking_not_found``,
                ``already_claimed``, ``booking_not_active``, ``not_eligible`` (a skill the
                booking requires is missing, judged inside the lock), or as ``view``.
        """
        summary = await self._group(principal, slug)
        if not summary.group.queue_free_pick_allowed:
            raise DomainError(ErrorCode.FREE_PICK_NOT_ALLOWED)
        async with self._uow.locked() as tx:
            booking = await _booking_of(tx, slug, booking_id)
            status = await tx.get_call_status(booking_id)
            if not summary.group.has_own_queue and not (status is not None and status.fallback):
                # A fallback queue (ADR 0082) holds only the bookings that fell back; any other
                # booking of the group is at the contact center and not ours to hand out.
                raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
            if booking.claim is not None:
                raise DomainError(ErrorCode.ALREADY_CLAIMED)
            await WorkEligibility(tx, principal, summary.organisation_slug).check(booking)
            return await self._claim(tx, principal, summary, booking, self._clock.now())

    async def claimed(
        self, principal: Principal, slug: str, booking_id: int, locale: str = FALLBACK_LOCALE
    ) -> ClaimedBooking:
        """Return a booking ``principal`` claimed, with its meeting link.

        Opening the claimed booking is where treatment starts in the own queue, so the skills
        are judged again here (ADR 0121 point 5, A35): a skill revoked since the claim gives
        ``not_eligible`` and no link. The claim itself stays; the agent or a supervisor
        releases it. The answers an agent may see (never PII) come with the labels in
        ``locale``.

        Raises:
            DomainError: ``not_claimed`` unless ``principal`` holds the claim,
                ``not_eligible``, or as ``claim``.
        """
        summary = await self._group(principal, slug)
        async with self._uow.read() as tx:
            booking = await _booking_of(tx, slug, booking_id)
            if not self._holds(principal, booking):
                raise DomainError(ErrorCode.NOT_CLAIMED)
            await WorkEligibility(tx, principal, summary.organisation_slug).check(booking)
            form = await tx.get_form(summary.group.id)
            status = await tx.get_call_status(booking_id)
            work = await self._work.of(tx, booking) if self._work else None
        return replace(_claimed(booking, form, locale, status), work=work)

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
            booking = await _booking_of(tx, slug, booking_id)
            mine = self._holds(principal, booking)
            if booking.claim is None or not (mine or supervisor):
                raise DomainError(ErrorCode.NOT_CLAIMED)
            if self._work is not None:
                await self._work.released(tx, booking, self._clock.now())
            await tx.release_claim(booking_id)
            await self._audit(tx, principal, summary, booking_id, RELEASED)
            await record_queue_released(tx, booking, summary.group, self._clock.now())

    async def start(self, principal: Principal, slug: str, booking_id: int) -> ClaimedBooking:
        """Start the treatment of request work ``principal`` claimed (ADR 0121 step 5).

        Inside the lock: the claim, the skills (A35), the lease and the pool's ``check_start``
        on the live staffing and every commitment (ADR 0122 decision 11). A start during a
        colleague's overrun goes ahead and is recorded on the work item.

        Raises:
            DomainError: ``not_claimed``, ``not_eligible``, ``booking_not_active``,
                ``lease_expired``, ``request_in_progress``, ``start_refused``, or as ``view``.
        """
        summary = await self._group(principal, slug)
        async with self._uow.locked() as tx:
            booking = await _booking_of(tx, slug, booking_id)
            if self._work is None or not self._holds(principal, booking):
                raise DomainError(ErrorCode.NOT_CLAIMED)
            await WorkEligibility(tx, principal, summary.organisation_slug).check(booking)
            work = await self._work.start(tx, summary.group, booking, self._clock.now())
            detail = STARTED_IN_CONFLICT if work.item and work.item.start_conflict else STARTED
            await self._audit(tx, principal, summary, booking_id, detail)
        return replace(_claimed(booking), work=work)

    async def complete(self, principal: Principal, slug: str, booking_id: int) -> None:
        """Complete request work: its pool unit is released once and the booking completed.

        The holder of the claim completes it; work the watchdog marked for recovery may also
        be completed by anyone who may view the group's bookings, like a release. A booking
        cancelled during treatment is still completed (design R7).

        Raises:
            DomainError: ``not_claimed``, ``request_not_started``, or as ``view``.
        """
        summary = await self._group(principal, slug)
        async with self._uow.locked() as tx:
            booking, _ = await group_booking(tx, slug, booking_id)
            work = await self._work.of(tx, booking) if self._work else None
            allowed = self._holds(principal, booking) or (
                recovering(work) and _supervises(principal, summary)
            )
            if self._work is None or work is None or not allowed:
                raise DomainError(ErrorCode.NOT_CLAIMED)
            await self._work.complete(tx, booking, self._clock.now())
            await self._audit(tx, principal, summary, booking_id, COMPLETED)

    async def _group(self, principal: Principal, slug: str) -> GroupSummary:
        """Authorise ``queue.work`` on the group and return it if it has an own queue."""
        summary = await self._admin.authorize_group(principal, Permission.QUEUE_WORK, slug)
        if not summary.group.queue_open:
            raise DomainError(ErrorCode.QUEUE_NOT_ENABLED)
        return summary

    async def _entries(
        self, tx: Transaction, principal: Principal, group: Group, now: datetime
    ) -> list[QueueEntry]:
        """Load the bookings around ``now`` with their call status and keep the visible ones."""
        return await load_entries(tx, scope_for(principal, Permission.QUEUE_WORK), group, now)

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
        await record_queue_claimed(tx, booking, summary.group, now)
        if self._staff_usage is not None:
            await self._staff_usage.claimed(tx, principal.subject, summary.group, now)
        work = None
        if self._work is not None:
            work = await self._work.claimed(tx, summary.group, booking, claim.claimed_by, now)
        return replace(_claimed(booking), work=work)

    def _holds(self, principal: Principal, booking: Booking) -> bool:
        """Return whether ``principal`` holds the claim on ``booking``."""
        return booking.claim is not None and booking.claim.claimed_by in agent_pseudonyms(
            self._keys, principal.subject
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
        await record_change(
            tx,
            self._clock.now(),
            principal,
            Permission.QUEUE_WORK,
            summary.organisation_slug,
            summary.group.slug,
            detail,
            booking_id=booking_id,
        )


async def load_entries(
    tx: Transaction, scope: Scope, group: Group, now: datetime
) -> list[QueueEntry]:
    """Load the group's queue entries at ``now`` inside ``scope`` (also the worker's wait watch).

    A booking ends one slot length after its start, so earlier ones have ended.
    """
    lower = now - timedelta(minutes=group.slot_minutes)
    upper = now + timedelta(minutes=group.queue_visible_from_minutes, seconds=1)
    overviews = await tx.list_bookings(scope, group.id, lower, upper)
    entries = (QueueEntry(o.booking, o.status) for o in overviews)
    if not group.has_own_queue:  # a fallback queue (ADR 0082): only the bookings that fell back
        entries = (e for e in entries if e.status.fallback)
    return visible(entries, group, now)


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


async def _lacking(judge: WorkEligibility, entries: list[QueueEntry]) -> dict[int, frozenset[int]]:
    """Return, per booking id, the ids of the skills the viewer lacks for it (empty: it fits).

    Eligibility is decided on these ids, never on the skills found to name them.
    """
    lacking: dict[int, frozenset[int]] = {}
    for entry in entries:
        verdict = await judge.of(entry.booking)
        lacking[_id(entry.booking)] = (
            verdict.skill_ids if isinstance(verdict, Missing) else frozenset()
        )
    return lacking


def _row(entry: QueueEntry, me: tuple[str, ...], overdue: bool) -> QueueRow:
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
        mine=claim is not None and claim.claimed_by in me,
        direct=entry.is_direct,
        overdue=overdue,
        video=entry.video,
        routing=booking.routing,
        fallback=entry.status.fallback,
    )


def _claimed(
    booking: Booking,
    form: FormDefinition | None = None,
    locale: str = FALLBACK_LOCALE,
    status: CallStatus | None = None,
) -> ClaimedBooking:
    """Return what the claiming agent sees of ``booking``: first name, answers, states."""
    contact = booking.contact
    status = status or new_status(_id(booking))
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
        video=video_state(booking, status),
        participant=participant_state(status),
    )


async def _booking_of(tx: Transaction, slug: str, booking_id: int) -> Booking:
    """Return the group's active booking ``booking_id``; another group's booking does not exist."""
    booking, _ = await group_booking(tx, slug, booking_id)
    if not booking.is_active:
        raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
    return booking


def _id(booking: Booking) -> int:
    """Return the ID of a stored booking; a missing ID is a programming error."""
    if booking.id is None:
        raise ValueError("booking without id")
    return booking.id
