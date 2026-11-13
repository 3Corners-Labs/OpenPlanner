"""Immediate intake for participants: take in, show and withdraw a request (ADR 0121 step 7).

The participant's side of work without an appointment. A service whose on-demand flow (its
default, or one named by its code) draws on a counted pool takes requests here; a legacy flow
keeps the direct call of ADR 0019 (``BookingService.start_direct``), which refuses a pool flow.

    offer    ─► the flow, the line now and the expected start (for the form)
    accept   ─► locked: flow ─► same intake key? the same request (A16) ─► closure
                ─► the service's form (ADR 0055) ─► admit_on_demand on the pool
                ─► request (queued, arrived) + request-level record (D1) + access hashes
    page     ─► the cookie's hash must match ─► status, place in line, expected start
    withdraw ─► locked: as page ─► cancelled; a claimed work item ends and frees its unit once

No personal data leaves this module except to the participant's own page (the first name), and
no step logs one. The rules are ``app.domain.immediate_intake``; the build brief is
``docs/service-flows-immediate-intake-design.md``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.booking import clean_contact_fields
from app.domain.closure import covering
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import ResolvedFlow
from app.domain.flow_resolver import resolve_flow
from app.domain.form_fields import Purpose, validate_answers
from app.domain.immediate_intake import (
    IntakeDecision,
    IntakeOutcome,
    RequestStatus,
    admit_on_demand,
    allows,
    new_access,
    place_in_line,
    status_of,
    valid_intake_key,
)
from app.domain.participant_links import hash_value
from app.domain.pool_availability import PoolTarget
from app.domain.pool_booking import PoolBookings
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.request_work import write_work
from app.domain.routing import ANSWER_PREFIX as ROUTING_PREFIX
from app.domain.scheduling import Group
from app.domain.service_flows import DispatchKind, FlowRevision, IntakeMode
from app.domain.service_requests import (
    RequestPersonalData,
    ServiceRequest,
    on_demand_request,
    withdraw,
)
from app.domain.services import ContactInput
from app.domain.tunable import value_for


@dataclass(frozen=True)
class ImmediateFlow:
    """The on-demand flow of a service that takes immediate work, with what intake needs.

    Attributes:
        group: The service.
        resolved: The flow and its revision in effect.
        revision: That revision (length, whether it asks for an email address).
        target: The pool and the length a request draws on.
    """

    group: Group
    resolved: ResolvedFlow
    revision: FlowRevision
    target: PoolTarget


@dataclass(frozen=True)
class ImmediateOffer:
    """What the form shows before anyone asks: the line now and its forecast.

    Attributes:
        flow: The flow that takes the request.
        waiting: How many wait or are claimed and not started.
        decision: Whether a request would be taken in now, with the expected start.
    """

    flow: ImmediateFlow
    waiting: int
    decision: IntakeDecision


@dataclass(frozen=True)
class Accepted:
    """A request taken in, and the value the participant's cookie gets.

    Attributes:
        request: The request (new, or the one the same intake key made, A16).
        cookie_value: The random value for the participant's cookie; only its hash is kept.
        created: Whether this call made the request.
        expected_start: The forecast start when it was made now; ``None`` on a retry.
    """

    request: ServiceRequest
    cookie_value: str = field(repr=False)
    created: bool
    expected_start: datetime | None = None


@dataclass(frozen=True)
class RequestPage:
    """What the participant's page shows: never an internal state, never more than a first name.

    Attributes:
        request: The request.
        group: Its service.
        status: What the participant is told.
        place: The place in line while it waits.
        expected_start: The forecast start while it waits; ``None`` when unknown.
        first_name: The first name it was asked under, if asked and not deleted.
    """

    request: ServiceRequest
    group: Group
    status: RequestStatus
    place: int | None
    expected_start: datetime | None
    first_name: str | None = field(default=None, repr=False)

    @property
    def withdrawable(self) -> bool:
        """Whether the participant may still withdraw it (choice I7)."""
        return self.status in (RequestStatus.WAITING, RequestStatus.CALLED)


class ImmediateRequests:
    """Takes in, shows and withdraws immediate requests of participants.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        pools: Loads a pool's staffing and commitments.
        lookahead_minutes: ``on_demand_admission_lookahead_minutes`` per organisation id.
        unresolved_hours: ``request_unresolved_hours`` per organisation id (the watchdog).
        phone_region: The default phone region per organisation id (ADR 0055).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        pools: PoolBookings,
        *,
        lookahead_minutes: int | Callable[[int], int],
        unresolved_hours: int | Callable[[int], int],
        phone_region: Callable[[int], str] = lambda _: "NL",
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._pools = pools
        self._lookahead = lookahead_minutes
        self._hours = unresolved_hours
        self._region = phone_region

    async def offer(self, group_slug: str, flow: str | None = None) -> ImmediateOffer | None:
        """Return the line of the service's immediate flow now, ``None`` when it has none.

        ``None`` means the on-demand path is the legacy direct call (or nothing): the page
        shows that instead.

        Raises:
            DomainError: ``group_not_found``, ``flow_not_available`` for a named flow that does
                not resolve or does not take immediate work.
        """
        async with self._uow.read() as tx:
            found = await self._flow(tx, group_slug, flow)
            if found is None:
                return None
            line = await tx.waiting_immediate(found.target.pool_id)
            decision = await self._admission(
                tx, found.group, found.target, self._clock.now(), waiting=len(line)
            )
        return ImmediateOffer(found, len(line), decision)

    async def accept(
        self,
        *,
        group_slug: str,
        contact: ContactInput,
        intake_key: str,
        flow: str | None = None,
    ) -> Accepted:
        """Take in an immediate request now, or return the one this intake key made (A16).

        Raises:
            DomainError: ``group_not_found``, ``flow_not_available`` (also for a service whose
                on-demand flow is the legacy direct call), ``invalid_contact`` (also for a
                missing or malformed intake key, field ``intake_key``), ``invalid_answer``,
                ``group_closed``, ``on_demand_full`` or ``on_demand_unavailable``.
        """
        if not valid_intake_key(intake_key):
            raise DomainError(ErrorCode.INVALID_CONTACT, "intake_key")
        async with self._uow.locked() as tx:
            found = await self._flow(tx, group_slug, flow)
            if found is None:
                raise DomainError(ErrorCode.FLOW_NOT_AVAILABLE, "flow")
            group = found.group
            again = await tx.get_request_by_intake_key(group.id, hash_value(intake_key))
            if again is not None:
                return await _retried(tx, again, intake_key)
            now = self._clock.now()
            if covering(await tx.get_closures(group.id, now), now) is not None:
                raise DomainError(ErrorCode.GROUP_CLOSED)
            record = await self._record(tx, found, contact, now)
            subject = group.routing.choose(contact.answers).subject
            line = await tx.waiting_immediate(found.target.pool_id)
            decision = await self._admission(tx, found.group, found.target, now, waiting=len(line))
            if decision.outcome is IntakeOutcome.FULL:
                raise DomainError(ErrorCode.ON_DEMAND_FULL)
            if not decision.accepted:
                raise DomainError(ErrorCode.ON_DEMAND_UNAVAILABLE)
            access, value = new_access(intake_key)
            request = on_demand_request(
                organisation_id=group.organisation_id,
                group_id=group.id,
                flow=found.resolved.ref,
                pool_id=found.target.pool_id,
                subject=subject,
                access=access,
                now=now,
                unresolved_hours=value_for(self._hours, group.organisation_id),
            )
            stored = await tx.add_service_request(request, record)
        return Accepted(stored, value, created=True, expected_start=decision.expected_start)

    async def page(self, public_ref: str, cookie_value: str | None) -> RequestPage:
        """Return the participant's page of the request, for the browser that holds it.

        Raises:
            DomainError: ``link_required`` without the cookie, for a request of someone else, one
                that does not exist and one whose page closed: the same answer (A31).
        """
        now = self._clock.now()
        async with self._uow.read() as tx:
            request = await self._held(tx, public_ref, cookie_value, now)
            return await self._page(tx, request, now)

    async def withdraw(self, public_ref: str, cookie_value: str | None) -> RequestPage:
        """Withdraw the request (choice I7); withdrawing a finished one changes nothing.

        Raises:
            DomainError: ``link_required`` as ``page``, ``request_in_progress`` once treatment
                started.
        """
        now = self._clock.now()
        async with self._uow.locked() as tx:
            request = await self._held(tx, public_ref, cookie_value, now)
            item = await tx.get_active_work_item(request.id or 0)
            changed, ended = withdraw(request, item, now=now)
            if changed is not request:
                await write_work(tx, request, item, changed, ended)
            return await self._page(tx, changed, now)

    async def _flow(
        self, tx: Transaction, group_slug: str, code: str | None
    ) -> ImmediateFlow | None:
        """Return the service's flow for immediate work, ``None`` for the legacy direct call.

        Raises:
            DomainError: ``group_not_found``, ``flow_not_available`` for a named flow that does
                not resolve, or for a pool flow that does not dispatch to the own queue (I8).
        """
        group = await tx.get_group(group_slug)
        if group is None or group.is_archived:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        resolved = await resolve_flow(tx, group.id, IntakeMode.ON_DEMAND, code)
        if resolved is None or resolved.legacy:
            if code is not None and resolved is None:
                raise DomainError(ErrorCode.FLOW_NOT_AVAILABLE, "flow")
            return None
        revision = await tx.flow_revision(resolved.flow_id, resolved.revision)
        if (
            revision is None
            or revision.dispatch is not DispatchKind.OWN_QUEUE
            or resolved.capacity_pool_id is None
            or resolved.duration_minutes is None
        ):
            raise DomainError(ErrorCode.FLOW_NOT_AVAILABLE, "flow")
        target = PoolTarget(resolved.capacity_pool_id, resolved.duration_minutes)
        return ImmediateFlow(group, resolved, revision, target)

    async def _admission(
        self,
        tx: Transaction,
        group: Group,
        target: PoolTarget,
        now: datetime,
        *,
        waiting: int | None,
    ) -> IntakeDecision:
        """Judge a request on the pool now: waiting limit, then a start within the look-ahead.

        ``waiting`` ``None`` asks for the forecast only: the waiting limit is not applied.
        """
        lookahead = value_for(self._lookahead, group.organisation_id)
        upper = now + timedelta(minutes=lookahead)
        scene = await self._pools.on_demand_scene(tx, group, target, now, upper)
        if scene is None:
            return IntakeDecision(IntakeOutcome.UNAVAILABLE)
        limit = None if waiting is None else await tx.get_pool_max_waiting(target.pool_id)
        return admit_on_demand(
            scene.supply,
            scene.commitments,
            now=now,
            duration_minutes=target.duration_minutes,
            buffer_minutes=scene.buffer_minutes,
            waiting=waiting or 0,
            max_waiting=limit,
            lookahead_minutes=lookahead,
        )

    async def _record(
        self, tx: Transaction, found: ImmediateFlow, contact: ContactInput, now: datetime
    ) -> RequestPersonalData:
        """Validate the service's form and return the request-level record (D1, ADR 0055).

        The email address only when the flow asks for it (``collects_email``); the answers an
        agent or planner may see, as for a booking. Nothing is invented for what was not asked.

        Raises:
            DomainError: ``invalid_contact`` or ``invalid_answer`` naming the field.
        """
        group = found.group
        region = self._region(group.organisation_id)
        answers = validate_answers(
            await tx.get_form(group.id),
            {k: v for k, v in contact.answers.items() if not k.startswith(ROUTING_PREFIX)},
            today=now.astimezone(group.tz).date(),
            region=region,
        )
        first, last, email, phone = clean_contact_fields(
            first_name=contact.first_name,
            last_name=contact.last_name,
            email=contact.email if found.revision.collects_email else None,
            phone=contact.phone,
            phone_mode=group.phone_mode,
            region=region,
        )
        kept = answers if answers.visible(Purpose.PLANNER_VIEW) else None
        return RequestPersonalData(first, last, email, phone, kept)

    async def _held(
        self, tx: Transaction, public_ref: str, cookie_value: str | None, now: datetime
    ) -> ServiceRequest:
        """Return the request if this browser holds it; ``link_required`` otherwise."""
        request = await tx.get_request_by_ref(public_ref) if len(public_ref) <= 32 else None
        if request is None or not allows(request, cookie_value, now):
            raise DomainError(ErrorCode.LINK_REQUIRED)
        return request

    async def _page(self, tx: Transaction, request: ServiceRequest, now: datetime) -> RequestPage:
        """Build the participant's page of ``request`` inside ``tx``."""
        group = await tx.get_group_by_id(request.group_id)
        if group is None or request.pool_id is None:
            raise DomainError(ErrorCode.LINK_REQUIRED)
        status = status_of(request)
        place = expected = None
        if status is RequestStatus.WAITING:
            line = await tx.waiting_immediate(request.pool_id)
            place = place_in_line(line, request)
            revision = await tx.flow_revision(request.flow.flow_id, request.flow.revision)
            if revision is not None and revision.duration_minutes is not None:
                target = PoolTarget(request.pool_id, revision.duration_minutes)
                forecast = await self._admission(tx, group, target, now, waiting=None)
                expected = forecast.expected_start
        record = await tx.get_request_personal_data(request.id or 0)
        first_name = record.first_name if record is not None else None
        return RequestPage(request, group, status, place, expected, first_name)


async def _retried(tx: Transaction, request: ServiceRequest, intake_key: str) -> Accepted:
    """Return the request a retried intake made (A16), with a fresh cookie value.

    Only while it is not finished and its page still opens: an old form sent again after the
    request was done, withdrawn or deleted makes no new request and takes over nothing.

    Raises:
        DomainError: ``invalid_contact`` (field ``intake_key``): the page offers a fresh form.
    """
    access, value = new_access(intake_key)
    if request.terminal or not await tx.set_request_access(request.id or 0, access.access_hash):
        raise DomainError(ErrorCode.INVALID_CONTACT, "intake_key")
    return Accepted(request, value, created=False)
