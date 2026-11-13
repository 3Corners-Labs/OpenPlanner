"""Immediate work in the own queue: list, claim, start, complete, release (ADR 0121 step 7).

The staff side of requests without an appointment. Every use case authorises ``queue.work`` on
the service (ADR 0014) and works only on that service's immediate requests; one of another
service does not exist here (``request_not_found``). Like the appointments of the queue
(``QueueService``), a claim needs the right, the skills (``WorkEligibility``, read inside the
lock, A09) and work that is free; unlike them, the claim of immediate work **acquires a unit of
its pool**: ``check_start`` with the intake ``on_demand`` under the lock, and the work item
holds the unit from the claim (ADR 0122 section 4). The start checks again with that unit left
out (A35); immediate work never starts with a conflict (P8 is for appointments only).

    view     ─► open immediate requests, oldest first: first name only, skills named (S3)
    claim    ─► locked: the first one the agent may take, or a chosen one (free pick)
                ─► skills ─► check_start(on_demand) ─► work item with unit and lease
    claimed  ─► the holder: first name and the answers an agent may see (never PII)
    start    ─► locked: holder, skills, lease ─► check_start without its own unit ─► started
    complete ─► holder, or a supervisor in recovery ─► unit released once
    release  ─► holder or supervisor, not started ─► unit released once, back in the line

Agents are recorded as pseudonyms (``tokens.agent_pseudonym``); audit details carry the
request's id, never a name.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.admin import AdminService, record_change
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import FALLBACK_LOCALE, LabelledAnswer, Purpose, labelled_answers
from app.domain.pool_accounting import StartOutcome, interval_end
from app.domain.pool_availability import PoolTarget
from app.domain.pool_booking import PoolBookings
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.queue_service import queue_group, supervises
from app.domain.request_work import write_work
from app.domain.scheduling import GroupSummary
from app.domain.service_requests import (
    HandlingState,
    ServiceRequest,
    WorkItem,
    check_startable,
    claim,
    complete,
    release,
    start,
)
from app.domain.skills import Missing, Skill
from app.domain.tokens import agent_pseudonym, agent_pseudonyms
from app.domain.tunable import value_for
from app.domain.work_eligibility import WorkEligibility

CLAIMED = "queue.request_claimed"
RELEASED = "queue.request_released"
STARTED = "queue.request_started"
COMPLETED = "queue.request_completed"
"""Audit details of immediate work, followed by ``:`` and the request's id."""


@dataclass(frozen=True)
class ImmediateRow:
    """One immediate request in the queue as an agent sees it: never email, phone or answers.

    Attributes:
        request_id: The request.
        first_name: The first name it was asked under; empty when none was asked or it is gone.
        since: When it was taken in (the wait counts from here).
        handling: Waiting, claimed, in treatment or marked for recovery.
        claimed: Someone holds it.
        mine: The viewing agent holds it.
        eligible: The viewer holds every skill it requires.
        missing: The skills the viewer lacks for it, to name them (choice S3).
    """

    request_id: int
    first_name: str = field(repr=False)
    since: datetime
    handling: HandlingState
    claimed: bool
    mine: bool
    eligible: bool = True
    missing: tuple[Skill, ...] = ()


@dataclass(frozen=True)
class ClaimedRequest:
    """Immediate work an agent holds, with what they may see of it.

    Attributes:
        request: The request.
        item: Its active work item.
        first_name: The first name it was asked under, if any.
        answers: Answers to the service's custom fields an agent may see (never PII, ADR 0055).
    """

    request: ServiceRequest
    item: WorkItem | None
    first_name: str = field(default="", repr=False)
    answers: tuple[LabelledAnswer, ...] = field(default=(), repr=False)


class RequestQueue:
    """Lets agents work the immediate requests of a service's own queue.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises every use case and audits refusals.
        pools: Judges a start on the request's pool.
        pseudonym_key: Key of the agents' pseudonyms (``keys.agent_pseudonym_key``).
        previous_pseudonym_key: The previous key during a rotation (ADR 0094 point 4).
        lease_minutes: ``assignment_start_lease_minutes`` per organisation id.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        pools: PoolBookings,
        pseudonym_key: bytes,
        previous_pseudonym_key: bytes | None = None,
        *,
        lease_minutes: int | Callable[[int], int] = 5,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._pools = pools
        self._key = pseudonym_key
        self._keys = (pseudonym_key, *([previous_pseudonym_key] if previous_pseudonym_key else []))
        self._lease = lease_minutes

    async def view(self, principal: Principal, slug: str) -> list[ImmediateRow]:
        """Return the service's open immediate requests, oldest first (choice I9).

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``queue_not_enabled``.
        """
        summary = await self._group(principal, slug)
        me = agent_pseudonyms(self._keys, principal.subject)
        rows = []
        async with self._uow.read() as tx:
            judge = WorkEligibility(tx, principal, summary.organisation_slug)
            for request in await tx.open_immediate(summary.group.id):
                item = await tx.get_active_work_item(request.id or 0)
                verdict = await judge.of_work(request.flow, request.subject)
                missing = verdict.skill_ids if isinstance(verdict, Missing) else frozenset()
                record = await tx.get_request_personal_data(request.id or 0)
                rows.append(
                    ImmediateRow(
                        request_id=request.id or 0,
                        first_name=(record.first_name or "") if record else "",
                        since=request.arrived_at or request.created_at,
                        handling=request.handling,
                        claimed=item is not None,
                        mine=item is not None and item.assigned_to in me,
                        eligible=not missing,
                        missing=await judge.skills(missing) if missing else (),
                    )
                )
        return rows

    async def claim(
        self, principal: Principal, slug: str, request_id: int | None = None
    ) -> ClaimedRequest:
        """Claim immediate work: the first waiting one the agent may take, or a chosen one.

        A chosen one needs ``queue_free_pick_allowed``, like an appointment. The claim takes a
        unit of the request's pool: refused while every unit is held for appointments or
        running work (A17).

        Raises:
            DomainError: ``free_pick_not_allowed``, ``request_not_found``, ``queue_empty``,
                ``not_eligible``, ``already_claimed``, ``booking_not_active`` (finished),
                ``start_refused``, or as ``view``.
        """
        summary = await self._group(principal, slug)
        if request_id is not None and not summary.group.queue_free_pick_allowed:
            raise DomainError(ErrorCode.FREE_PICK_NOT_ALLOWED)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            judge = WorkEligibility(tx, principal, summary.organisation_slug)
            if request_id is None:
                request = await self._first(tx, summary, judge)
            else:
                request = await self._request(tx, summary, request_id)
                await judge.check_work(request.flow, request.subject)
            active = await tx.get_active_work_item(request.id or 0)
            if active is not None:
                raise DomainError(ErrorCode.ALREADY_CLAIMED)
            target = await self._target(tx, request)
            buffer = self._pools.wrap_up(summary.group.organisation_id)
            check = await self._pools.check_on_demand_start(
                tx, summary.group, target, now, buffer_minutes=buffer
            )
            if check.outcome is StartOutcome.REFUSED:
                raise DomainError(ErrorCode.START_REFUSED)
            claimed, item = claim(
                request,
                None,
                now=now,
                agent=agent_pseudonym(self._key, principal.subject),
                lease_minutes=value_for(self._lease, summary.group.organisation_id),
                planned_until=interval_end(now, target.duration_minutes, buffer),
            )
            if not await tx.update_service_request(claimed, request.handling):
                raise DomainError(ErrorCode.ALREADY_CLAIMED)
            stored = await tx.add_work_item(item)
            await self._audit(tx, principal, summary, CLAIMED, claimed)
            return await self._claimed(tx, summary, claimed, stored)

    async def claimed(
        self, principal: Principal, slug: str, request_id: int, locale: str = FALLBACK_LOCALE
    ) -> ClaimedRequest:
        """Return immediate work ``principal`` holds, with the answers an agent may see.

        The skills are judged again (A35): a skill revoked since the claim gives
        ``not_eligible``; the claim stays until it is released.

        Raises:
            DomainError: ``request_not_found``, ``not_claimed``, ``not_eligible``, or as
                ``view``.
        """
        summary = await self._group(principal, slug)
        async with self._uow.read() as tx:
            request = await self._request(tx, summary, request_id)
            item = await tx.get_active_work_item(request_id)
            if not self._holds(principal, item):
                raise DomainError(ErrorCode.NOT_CLAIMED)
            judge = WorkEligibility(tx, principal, summary.organisation_slug)
            await judge.check_work(request.flow, request.subject)
            return await self._claimed(tx, summary, request, item, locale)

    async def start(self, principal: Principal, slug: str, request_id: int) -> ClaimedRequest:
        """Start the treatment of immediate work ``principal`` holds, judged on its pool now.

        Raises:
            DomainError: ``request_not_found``, ``not_claimed``, ``not_eligible``,
                ``lease_expired``, ``request_in_progress``, ``start_refused``, or as ``view``.
        """
        summary = await self._group(principal, slug)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            request = await self._request(tx, summary, request_id)
            item = await tx.get_active_work_item(request_id)
            if not self._holds(principal, item) or item is None:
                raise DomainError(ErrorCode.NOT_CLAIMED)
            judge = WorkEligibility(tx, principal, summary.organisation_slug)
            await judge.check_work(request.flow, request.subject)
            startable = check_startable(item, now)
            target = await self._target(tx, request)
            buffer = _fixed_buffer(startable, target)
            check = await self._pools.check_on_demand_start(
                tx, summary.group, target, now, buffer_minutes=buffer, own=request.key
            )
            if check.outcome is StartOutcome.REFUSED:
                raise DomainError(ErrorCode.START_REFUSED)
            started, begun = start(
                request,
                item,
                now=now,
                pool_id=target.pool_id,
                planned_until=interval_end(now, target.duration_minutes, buffer),
                conflict=False,
            )
            await write_work(tx, request, item, started, begun)
            await self._audit(tx, principal, summary, STARTED, started)
            return await self._claimed(tx, summary, started, begun)

    async def complete(self, principal: Principal, slug: str, request_id: int) -> None:
        """Complete immediate work: its unit is released once (A27: explicit evidence).

        The holder completes it; work marked for recovery may also be completed by a
        supervisor (who may view the service's bookings).

        Raises:
            DomainError: ``request_not_found``, ``not_claimed``, ``request_not_started``, or as
                ``view``.
        """
        summary = await self._group(principal, slug)
        async with self._uow.locked() as tx:
            request = await self._request(tx, summary, request_id)
            item = await tx.get_active_work_item(request_id)
            recovering = request.handling is HandlingState.RECOVERY_REQUIRED
            if not (
                self._holds(principal, item) or (recovering and supervises(principal, summary))
            ):
                raise DomainError(ErrorCode.NOT_CLAIMED)
            done, ended = complete(request, item, now=self._clock.now())
            await write_work(tx, request, item, done, ended)
            await self._audit(tx, principal, summary, COMPLETED, done)

    async def release(self, principal: Principal, slug: str, request_id: int) -> None:
        """Put claimed immediate work back in the line; its unit is released once.

        The holder may, and so may a supervisor (who may view the service's bookings).

        Raises:
            DomainError: ``request_not_found``, ``not_claimed``, ``request_in_progress``, or as
                ``view``.
        """
        summary = await self._group(principal, slug)
        async with self._uow.locked() as tx:
            request = await self._request(tx, summary, request_id)
            item = await tx.get_active_work_item(request_id)
            if item is None or not (self._holds(principal, item) or supervises(principal, summary)):
                raise DomainError(ErrorCode.NOT_CLAIMED)
            back, ended = release(request, item, now=self._clock.now())
            await write_work(tx, request, item, back, ended)
            await self._audit(tx, principal, summary, RELEASED, back)

    async def _group(self, principal: Principal, slug: str) -> GroupSummary:
        """Authorise ``queue.work`` on the service and return it if it has an own queue."""
        return await queue_group(self._admin, principal, slug)

    async def _request(
        self, tx: Transaction, summary: GroupSummary, request_id: int
    ) -> ServiceRequest:
        """Return the service's immediate request; any other does not exist here."""
        request = await tx.get_service_request(request_id)
        if request is None or not request.immediate or request.group_id != summary.group.id:
            raise DomainError(ErrorCode.REQUEST_NOT_FOUND)
        return request

    async def _first(
        self, tx: Transaction, summary: GroupSummary, judge: WorkEligibility
    ) -> ServiceRequest:
        """Return the oldest waiting request the agent may take (choice I9).

        Raises:
            DomainError: ``queue_empty`` when none waits, ``not_eligible`` when none fits.
        """
        waiting = [
            r
            for r in await tx.open_immediate(summary.group.id)
            if r.handling is HandlingState.QUEUED
        ]
        if not waiting:
            raise DomainError(ErrorCode.QUEUE_EMPTY)
        for request in waiting:
            if not isinstance(await judge.of_work(request.flow, request.subject), Missing):
                return request
        raise DomainError(ErrorCode.NOT_ELIGIBLE)

    async def _target(self, tx: Transaction, request: ServiceRequest) -> PoolTarget:
        """Return the pool and length the request draws on (its recorded revision)."""
        revision = await tx.flow_revision(request.flow.flow_id, request.flow.revision)
        if revision is None or revision.duration_minutes is None or request.pool_id is None:
            raise DomainError(ErrorCode.START_REFUSED)
        return PoolTarget(request.pool_id, revision.duration_minutes)

    async def _claimed(
        self,
        tx: Transaction,
        summary: GroupSummary,
        request: ServiceRequest,
        item: WorkItem | None,
        locale: str = FALLBACK_LOCALE,
    ) -> ClaimedRequest:
        """Return what the holder sees: the first name and the answers an agent may see."""
        record = await tx.get_request_personal_data(request.id or 0)
        form = await tx.get_form(summary.group.id)
        answers = (
            labelled_answers(record.answers, form, Purpose.AGENT_VIEW, locale)
            if record is not None and form is not None
            else ()
        )
        first_name = (record.first_name or "") if record is not None else ""
        return ClaimedRequest(request, item, first_name, answers)

    def _holds(self, principal: Principal, item: WorkItem | None) -> bool:
        """Return whether ``principal`` holds ``item`` (its pseudonym, either key)."""
        return (
            item is not None
            and item.assigned_to is not None
            and item.assigned_to in agent_pseudonyms(self._keys, principal.subject)
        )

    async def _audit(
        self,
        tx: Transaction,
        principal: Principal,
        summary: GroupSummary,
        detail: str,
        request: ServiceRequest,
    ) -> None:
        """Record the change in the same transaction, with the request's id (never a name)."""
        await record_change(
            tx,
            self._clock.now(),
            principal,
            Permission.QUEUE_WORK,
            summary.organisation_slug,
            summary.group.slug,
            f"{detail}:{request.id}",
        )


def _fixed_buffer(item: WorkItem, target: PoolTarget) -> int:
    """Return the wrap-up the claim fixed into the work item (``planned_until - assigned_at``).

    Raises:
        DomainError: ``start_refused`` for a work item without the unit of a claim.
    """
    if item.planned_until is None:
        raise DomainError(ErrorCode.START_REFUSED)
    held = (item.planned_until - item.assigned_at) // timedelta(minutes=1)
    return held - target.duration_minutes
