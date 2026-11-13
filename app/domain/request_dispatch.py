"""Request work at the contact center: dispatch, platform events, late tasks, fencing.

ADR 0121 step 6b; build brief ``docs/service-flows-contact-center-contract-design.md`` section 11.
A booking of a new flow on a counted pool is request work (``RequestWork.handles``). When its
flow dispatches to the contact center, the dispatch job opens an *attempt*: a work item of
channel ``contact_center`` (``app.domain.contact_center_attempts``). One active work item per
request is the fence: the own queue and the contact center never hold the same request.

    dispatch job ─► lock: request, attempt dispatching ─► create_task
        ok ─► task on the booking, attempt dispatched
        refused / not sent ─► attempt failed, request queued; the job retries or falls back
        outcome unknown ─► attempt reconciliation_required; never retried
    platform event of the booking's task ─► connected = start, ended = complete, failed = queue
    platform event of an unknown task, origin = a booking reference
        ─► attempt in doubt: the task is adopted (no second task)
        ─► attempt fenced: the late task is ended by the end-task job (ADR 0021)
    fence round ─► attempts in doubt past ``contact_center_reconcile_minutes``: fenced; the
        request goes to the own queue and the fallback tells the team (ADR 0082)

The task id is kept on the booking (``contact_center_task``), as for every booking that reaches
the contact center, so the timeline, the end-task job and the purge work unchanged.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.domain import contact_center_attempts as attempts
from app.domain.booking import Booking, BookingStatus, booking_id_of, stored_id
from app.domain.calls import ContactCenterTask, EventKind
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef, TaskKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.fallback import FALLBACK, FallbackService
from app.domain.integration import IntegrationService
from app.domain.job_plan import JobType, PlannedJob
from app.domain.pool_accounting import StartOutcome
from app.domain.ports import (
    Clock,
    ContactCenterConnections,
    ContactCenterDispatcher,
    IntegrationError,
    Transaction,
    UnitOfWork,
)
from app.domain.request_work import RequestWork, WorkJudge
from app.domain.scheduling import Group
from app.domain.service_flows import DispatchKind, FlowRevision
from app.domain.service_requests import (
    IN_DOUBT,
    ORPHANED,
    ServiceRequest,
    WorkChannel,
    WorkItem,
    WorkItemState,
)

OWN_QUEUE = "own_queue"
"""Skip reason: the request's flow uses the own queue, or the own queue holds the request (the
value of the dispatch job's ``OWN_QUEUE``)."""
HELD = "already_done"
"""Skip reason: the request already has an active work item (the job's ``ALREADY_DONE``)."""
NO_DISPATCH = "no_dispatch"
"""Skip reason: the service has no complete routing, or no dispatcher for its platform."""
IN_DOUBT_SKIP = "outcome_unknown"
"""The create's outcome is unknown: the attempt waits for reconciliation, the job ends."""


@dataclass(frozen=True)
class _Write:
    """New states of a request and its attempt, with the states they were read in."""

    request: ServiceRequest
    seen_request: ServiceRequest
    item: WorkItem
    seen_item: WorkItemState


async def _write(tx: Transaction, change: _Write) -> None:
    """Write the attempt and the request, each conditional on the state it was read in.

    Under the write lock a condition fails only when an invariant is already broken; then the
    whole transaction is refused rather than half written.

    Raises:
        DomainError: ``already_claimed`` when either write lost its condition.
    """
    if not await tx.update_work_item(change.item, change.seen_item):
        raise DomainError(ErrorCode.ALREADY_CLAIMED)
    if not await tx.update_service_request(change.request, change.seen_request.handling):
        raise DomainError(ErrorCode.ALREADY_CLAIMED)


def _out_of_order(error: DomainError) -> bool:
    """Whether ``error`` is an attempt's transition refusing an event it cannot take."""
    return error.code is ErrorCode.NOT_CLAIMED


class AttemptEvents:
    """Platform events on contact-center attempts, inside the integration's locked transaction.

    Args:
        work: The request work of the own queue: the pool slot and the start check.
        window_minutes: ``contact_center_reconcile_minutes``: how long after a fence a late
            task of that attempt is still recognised (and ended) as ours.
        own_connection: Whether an organisation has its own contact-center connection (ADR 0032
            point 9); its tasks never come to the installation's webhook address. ``None``: no
            organisation has one (an installation without the connections' key).
    """

    def __init__(
        self,
        work: RequestWork,
        window_minutes: Callable[[], int],
        own_connection: Callable[[int], Awaitable[bool]] | None = None,
    ) -> None:
        """Store the collaborators."""
        self._work = work
        self._window = window_minutes
        self._own_connection = own_connection

    async def on_task_event(
        self, tx: Transaction, booking: Booking, kind: EventKind, at: datetime
    ) -> bool:
        """Apply a platform event of ``booking``'s task to its active attempt, if it has one.

        A booking without a request, or whose active work item is not the contact center's,
        is left alone: the event is on its timeline and nothing else changes (E8). An attempt
        that ends before it connected (``task:ended`` straight after the task was made) is
        completed all the same: the platform says the work is done, and it held no pool unit.

        Returns:
            Whether the request went back to the own queue (``task:failed``): the caller tells
            the team through the fallback (ADR 0082), outside its transaction.
        """
        request = await tx.get_request_of_booking(stored_id(booking))
        item = await tx.get_active_work_item(request.id or 0) if request else None
        if request is None or item is None or item.channel is not WorkChannel.CONTACT_CENTER:
            return False
        try:
            if kind is EventKind.TASK_CONNECTED:
                await self._connected(tx, booking, request, item, at)
            elif kind is EventKind.TASK_ENDED:
                new_request, new_item = attempts.ended(request, item, now=at)
                await _write(tx, _Write(new_request, request, new_item, item.state))
                if booking.status is BookingStatus.CONFIRMED:
                    await tx.save_booking(replace(booking, status=BookingStatus.COMPLETED))
            elif kind is EventKind.TASK_FAILED:
                new_request, new_item = attempts.failed(request, item, now=at)
                await _write(tx, _Write(new_request, request, new_item, item.state))
                return True
        except DomainError as error:
            if not _out_of_order(error):
                raise
            # An event out of order (connected twice, ended before it was dispatched): ignored.
        return False

    async def _connected(
        self,
        tx: Transaction,
        booking: Booking,
        request: ServiceRequest,
        item: WorkItem,
        at: datetime,
    ) -> None:
        """Start the attempt's treatment on the booking's pool; an overrun is recorded."""
        slot = await self._work.pool_slot(tx, booking)
        group = await tx.get_group_by_id(booking.group_id)
        if slot is None or slot.occupied_until is None or group is None:
            raise DomainError(ErrorCode.NOT_CLAIMED)  # request work always has its pool slot
        check = await self._work.check_start(tx, group, slot, stored_id(booking), at)
        new_request, new_item = attempts.connected(
            request,
            item,
            now=at,
            pool_id=slot.capacity_pool_id or 0,
            planned_until=at + (slot.occupied_until - slot.start),
            conflict=check is not StartOutcome.MAY_START,
        )
        await _write(tx, _Write(new_request, request, new_item, item.state))

    async def adopt(
        self,
        tx: Transaction,
        origin: str,
        task_id: str,
        provider: ContactCenterKind,
        at: datetime,
        organisation_id: int | None = None,
    ) -> Booking | None:
        """Match a task OpenPlanner did not know to an attempt by its ``origin`` (a reference).

        Only a booking without a task, of a service routed to ``provider``, inside the
        organisation of the webhook address (``organisation_id``; at the installation's address
        only an organisation without its own connection), whose request has a contact-center
        attempt that never got a task, is matched; any other origin is not ours (``None``):

        * a confirmed booking whose attempt is ``reconciliation_required`` (the create returned
          with an unknown outcome): the task is adopted as the attempt's own, no second task;
        * an attempt fenced, or ended because the booking was cancelled, within the window: the
          task is an orphan, kept on the booking and ended at the platform by the end-task job
          (ADR 0021), so it never stays with an agent.

        An attempt still ``dispatching`` is never adopted: its create is still running and gives
        the task itself.
        """
        booking_id = booking_id_of(origin)
        booking = await tx.get_booking(booking_id) if booking_id is not None else None
        request = await tx.get_request_of_booking(stored_id(booking)) if booking else None
        if booking is None or request is None or booking.contact_center_task is not None:
            return None
        if not await self._ours(tx, booking, request, provider, organisation_id):
            return None
        # The kind comes from the create that made the task (stored before it was tried).
        task = ContactCenterTaskRef(task_id, provider, booking.attempted_task_kind)
        items = [
            i
            for i in await tx.work_items_of(request.id or 0)
            if i.channel is WorkChannel.CONTACT_CENTER
        ]
        doubt = next((i for i in items if i.state is WorkItemState.RECONCILIATION_REQUIRED), None)
        if doubt is not None and booking.status is BookingStatus.CONFIRMED:
            if not await tx.update_work_item(attempts.dispatched(doubt), doubt.state):
                return None
            await tx.save_task(stored_id(booking), task)
            return replace(booking, contact_center_task=task)
        horizon = at - timedelta(minutes=self._window())
        orphaned = doubt is not None or any(
            i.state in ORPHANED and i.ended_at is not None and i.ended_at >= horizon for i in items
        )
        if not orphaned:
            return None
        await tx.save_task(stored_id(booking), task)
        await tx.add_jobs(stored_id(booking), [PlannedJob(JobType.END_WXCC_TASK, at, None)])
        return replace(booking, contact_center_task=task)

    async def _ours(
        self,
        tx: Transaction,
        booking: Booking,
        request: ServiceRequest,
        provider: ContactCenterKind,
        organisation_id: int | None,
    ) -> bool:
        """Whether an event of ``provider`` at this webhook address may concern ``booking``.

        Its service must be routed to that platform, and the address must be its organisation's
        own, or the installation's while the organisation has no connection of its own (ADR 0032
        point 10): a reference names a booking of one installation and one organisation only.
        """
        group = await tx.get_group_by_id(booking.group_id)
        if (
            group is None
            or group.contact_center is None
            or group.contact_center.kind is not provider
        ):
            return False
        if organisation_id is not None:
            return request.organisation_id == organisation_id
        if self._own_connection is None:
            return True
        return not await self._own_connection(request.organisation_id)


def own_connection_of(
    connections: ContactCenterConnections | None,
) -> Callable[[int], Awaitable[bool]] | None:
    """Return whether an organisation has its own connection, read from ``connections``.

    ``None`` without the connections (an installation without their key): no organisation has
    one then, and every task comes to the installation's webhook address.
    """
    if connections is None:
        return None
    store = connections

    async def has_own(organisation_id: int) -> bool:
        """Whether ``organisation_id`` has a connection of its own."""
        return await store.get(organisation_id) is not None

    return has_own


TaskBuilder = Callable[[Booking, Group], Awaitable[ContactCenterTask]]
DispatcherOf = Callable[[Group], Awaitable[ContactCenterDispatcher | None]]


class RequestDispatch:
    """Dispatch the work of a request to the contact center, and fence attempts in doubt.

    Args:
        uow: Opens database transactions.
        work: Makes the request of a booking (the same rule as a claim).
        clock: Current time.
        task_of: Builds the neutral task of a booking (``CallService.task_for``).
        dispatcher_of: The dispatcher of a group's platform, ``None`` without one.
        integration: Records events on the booking's timeline.
        window_minutes: ``contact_center_reconcile_minutes`` (class C).
        fallback: Tells the team when work goes to the own queue (ADR 0082).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        work: RequestWork,
        clock: Clock,
        *,
        task_of: TaskBuilder,
        dispatcher_of: DispatcherOf,
        integration: IntegrationService,
        window_minutes: Callable[[], int],
        fallback: FallbackService | None = None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._work = work
        self._clock = clock
        self._task_of = task_of
        self._dispatcher_of = dispatcher_of
        self._integration = integration
        self._window = window_minutes
        self._fallback = fallback

    async def handles(self, booking: Booking) -> FlowRevision | None:
        """Return the revision of ``booking`` when it is request work, else ``None``.

        A booking that is not request work (no flow, a legacy flow, a service's own slot, a
        video flow) keeps the dispatch it has always had.
        """
        if booking.flow is None:
            return None
        async with self._uow.read() as tx:
            if not await WorkJudge(tx).handles(booking):
                return None
            return await tx.flow_revision(booking.flow.flow_id, booking.flow.revision)

    async def dispatch(self, booking: Booking, group: Group, revision: FlowRevision) -> str | None:
        """Hand the request of ``booking`` to the contact center; return why not, if not.

        Raises:
            IntegrationError: When the platform refused or could not be reached before the
                task was sent (the job retries, or its final failure falls back, ADR 0082).
            DomainError: ``integration_not_ready`` when the task cannot be built (an unmapped
                skill, a kind the platform does not carry).
        """
        if revision.dispatch is not DispatchKind.CONTACT_CENTER or booking.claim is not None:
            return OWN_QUEUE
        dispatcher = await self._dispatcher_of(group)
        if dispatcher is None or group.contact_center is None:
            return NO_DISPATCH
        if self._fallback is not None and await self._fallback.diverts(booking, group):
            return FALLBACK
        built = await self._task_of(booking, group)  # before the attempt: a refusal opens none
        opened = await self._open(booking, group, built.task_kind)
        if opened is None:
            return HELD
        try:
            task_id = await dispatcher.create_task(built)
        except IntegrationError as error:
            if error.ambiguous:
                await self._settle(opened, lambda r, i: (r, attempts.in_doubt(i)))
                await self._integration.record(
                    stored_id(booking), EventKind.INTEGRATION_ERROR, detail=IN_DOUBT_SKIP
                )
                return IN_DOUBT_SKIP
            await self._fail(opened)
            raise
        task = built.ref(task_id)
        async with self._uow.locked() as tx:
            await tx.save_task(stored_id(booking), task)
            current = await tx.get_active_work_item(opened.request.id or 0)
            if current is not None and current.id == opened.item.id and current.state in IN_DOUBT:
                await tx.update_work_item(attempts.dispatched(current), current.state)
            else:  # fenced while the create ran: the own queue holds it, the task is ended
                await tx.add_jobs(
                    stored_id(booking), [PlannedJob(JobType.END_WXCC_TASK, self._clock.now(), None)]
                )
        await self._integration.record(stored_id(booking), EventKind.TASK_CREATED)
        return None

    async def _open(self, booking: Booking, group: Group, task_kind: TaskKind) -> _Write | None:
        """Open the attempt under the lock; ``None`` when someone holds the request.

        The kind the create is tried as is stored with the attempt, before the platform
        answers, so that adoption of a task found later reads it back (ADR 0123 step 7a).
        """
        now = self._clock.now()
        async with self._uow.locked() as tx:
            request = await self._work.request_of(tx, group, booking, now)
            active = await tx.get_active_work_item(request.id or 0)
            try:
                new_request, item = attempts.open_attempt(request, active, now=now)
            except DomainError:
                return None
            if not await tx.update_service_request(new_request, request.handling):
                return None
            stored = await tx.add_work_item(item)
            await tx.save_task_attempt(stored_id(booking), task_kind)
        return _Write(new_request, new_request, stored, stored.state)

    async def _fail(self, opened: _Write) -> None:
        """End the attempt ``failed`` and queue the request again."""
        now = self._clock.now()
        await self._settle(opened, lambda r, i: attempts.failed(r, i, now=now))

    async def _settle(
        self,
        opened: _Write,
        change: Callable[[ServiceRequest, WorkItem], tuple[ServiceRequest, WorkItem]],
    ) -> None:
        """Apply ``change`` to the attempt as it is stored now, conditionally."""
        async with self._uow.locked() as tx:
            request = await tx.get_service_request(opened.request.id or 0)
            item = await tx.get_active_work_item(opened.request.id or 0)
            if request is None or item is None or item.id != opened.item.id:
                return
            new_request, new_item = change(request, item)
            await _write(tx, _Write(new_request, request, new_item, item.state))

    async def fence_round(self) -> int:
        """Fence every attempt in doubt past the window; return how many were fenced.

        Each fenced request goes to the own queue, and the fallback tells the team about its
        booking (ADR 0082 point 11). Counts only; no booking or participant is logged.
        """
        now = self._clock.now()
        window = self._window()
        async with self._uow.read() as tx:
            due = [
                i for i in await tx.work_items_in_doubt() if attempts.due_for_fence(i, now, window)
            ]
        fenced: list[int] = []
        for item in due:
            booking_id = await self._fence_one(item, now)
            if booking_id is not None:
                fenced.append(booking_id)
        for booking_id in fenced:
            await self._integration.record(booking_id, EventKind.INTEGRATION_ERROR, detail="fenced")
            if self._fallback is not None:
                await self._fallback.dispatch_failed(booking_id)
        return len(fenced)

    async def _fence_one(self, seen: WorkItem, now: datetime) -> int | None:
        """Fence one attempt in its own transaction; its booking id, or ``None`` when skipped.

        A request that finished meanwhile, or a write that lost its condition, skips this
        attempt only: its transaction is rolled back and the round goes on with the next.
        """
        try:
            async with self._uow.locked() as tx:
                item = await tx.get_active_work_item(seen.request_id)
                request = await tx.get_service_request(seen.request_id)
                if item is None or item.id != seen.id or request is None:
                    return None
                if request.booking_id is None:
                    return None
                new_request, new_item = attempts.fence(request, item, now=now)
                await _write(tx, _Write(new_request, request, new_item, item.state))
                return request.booking_id
        except DomainError:
            return None
