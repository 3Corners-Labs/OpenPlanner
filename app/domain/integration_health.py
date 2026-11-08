"""Evaluating connection health and reconciling task states in the worker (ADR 0082).

``IntegrationHealthService`` recomputes the health of every connection from the request events
and writes its transitions; ``TaskReconciler`` asks the contact center about tasks the webhooks
left stale; ``IntegrationHealthWatch`` runs both every ``task_reconcile_interval_minutes``. The
model and its rules are in ``app.domain.connection_health``.
"""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from app.domain.alerts import open_conditions
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.calls import EventKind
from app.domain.connection_health import (
    MISSED,
    RECHECK_RUNS,
    RECONCILE_LIMIT,
    RECONCILED,
    TASK_EVENTS,
    WEBHOOKS_MISSING,
    HealthEntry,
    HealthPolicy,
    InboundEvent,
    Platform,
    StaleTask,
    evaluate,
    health_event,
)
from app.domain.event_context import bind_booking, bind_level, trace_scope
from app.domain.integration import IntegrationService
from app.domain.ports import Clock, ContactCenterDispatcher, IntegrationError, UnitOfWork
from app.domain.tunable import value_of

NOTE_EVERY = timedelta(seconds=10)
"""Shortest gap between two notes of a platform's last webhook in one process (a fixed
protection of the write lock; the line is shown in minutes)."""


class IntegrationHealthService:
    """Evaluates and stores the health state, and reads it for the portal.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        policy: The thresholds, read on every evaluation (portal settings may change).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        policy: HealthPolicy | Callable[[], HealthPolicy],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._policy = policy
        self._noted: dict[Platform, datetime] = {}

    async def evaluate(self) -> tuple[HealthEntry, ...]:
        """Recompute every connection's health, store it and write its transitions."""
        now = self._clock.now()
        policy = value_of(self._policy)
        since = now - policy.window
        async with self._uow.locked() as tx:
            entries, changes = evaluate(
                await tx.request_samples(since),
                await tx.get_health(),
                now,
                policy,
                token_failed=await tx.token_refresh_failed_since(since),
                probe_failed=await tx.failed_probe_organisations(since),
            )
            await tx.save_health(entries, now)
            for before, after in changes:
                await tx.add_audit_event(health_event(before, after, now))
        return entries

    async def note_inbound(self, event: InboundEvent) -> None:
        """Store ``event`` as its platform's latest verified webhook, at most every few seconds.

        Org-wide Webex webhooks report every meeting of the organisation; writing each would
        take the write lock once more per event. A note within ``NOTE_EVERY`` of this process's
        previous one for the platform is skipped, so the line is at most that stale.
        """
        last = self._noted.get(event.platform)
        if last is not None and event.at - last < NOTE_EVERY:
            return
        self._noted[event.platform] = event.at
        async with self._uow.locked() as tx:
            await tx.save_inbound_event(event)

    async def overview(self) -> tuple[tuple[HealthEntry, ...], tuple[InboundEvent, ...]]:
        """Return the stored health of every connection and the last inbound events."""
        async with self._uow.read() as tx:
            return await tx.get_health(), await tx.get_inbound_events()


class TaskReconciler:
    """Reads stale tasks at the contact center and writes what the webhooks missed.

    Each read runs in a trace bound to the booking, its organisation and group, so its request
    event lands on the booking's page and counts for the right organisation (ADR 0068). A task
    read once is not read again for ``RECHECK_RUNS`` runs, so tasks the contact center no longer
    knows, or keeps in the same state, cannot crowd out newer ones under ``RECONCILE_LIMIT``.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        dispatcher: Reads a task's state (``read_task``); ``None`` without a contact center.
        integration: Writes a timeline event and the call status (``IntegrationService``).
        after: How long a task may go without a change (``task_reconcile_after_minutes``).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        dispatcher: ContactCenterDispatcher | None,
        integration: IntegrationService,
        after: timedelta | Callable[[], timedelta],
    ) -> None:
        """Store the collaborators; nothing has been read yet."""
        self._uow = uow
        self._clock = clock
        self._dispatcher = dispatcher
        self._integration = integration
        self._after = after
        self._read_at: dict[int, datetime] = {}

    async def run(self) -> int:
        """Reconcile the stale tasks; return how many states the webhooks had missed.

        A task the contact center no longer knows, or cannot be asked about now, is left as
        it is. Each organisation with a missed state gets one ``integration.webhooks_missing``
        failure with the count; one with an open failure whose run found none gets the success
        that closes the alert.
        """
        if self._dispatcher is None:
            return 0
        now = self._clock.now()
        after = value_of(self._after)
        async with self._uow.read() as tx:
            stale = await tx.stale_tasks(now - after, RECONCILE_LIMIT * RECHECK_RUNS)
        rest = now - after * RECHECK_RUNS
        self._read_at = {b: at for b, at in self._read_at.items() if at > rest}
        due = [t for t in stale if t.booking_id not in self._read_at][:RECONCILE_LIMIT]
        missed: dict[str | None, int] = {}
        for task in due:
            self._read_at[task.booking_id] = now
            kind = await self._read(task)
            missed.setdefault(task.organisation, 0)
            if kind is not None:
                await self._integration.record(task.booking_id, kind, detail=RECONCILED)
                missed[task.organisation] += 1
        await self._report(missed, now)
        return sum(missed.values())

    async def _read(self, task: StaleTask) -> EventKind | None:
        """Return the event that brings the booking to the task's state, if it differs."""
        if self._dispatcher is None:  # pragma: no cover - run() returns before
            return None
        with trace_scope():
            bind_level(task.organisation, task.group)
            bind_booking(task.booking_id)
            try:
                snapshot = await self._dispatcher.read_task(task.task_id)
            except IntegrationError:
                return None
        if snapshot.state is task.state:
            return None
        return TASK_EVENTS.get(snapshot.state)

    async def _report(self, missed: Mapping[str | None, int], now: datetime) -> None:
        """Write the failure per organisation with a miss, and the success that ends one."""
        open_before = {a.organisation for a in await open_conditions(self._uow, WEBHOOKS_MISSING)}
        async with self._uow.locked() as tx:
            for organisation, count in missed.items():
                if count == 0 and organisation not in open_before:
                    continue
                await tx.add_audit_event(
                    AuditEvent(
                        now,
                        SYSTEM_ACTOR,
                        WEBHOOKS_MISSING,
                        Outcome.FAILURE if count else Outcome.SUCCESS,
                        MISSED if count else "",
                        organisation,
                        None,
                        f"count:{count}",
                        Source.SYSTEM,
                    )
                )


class IntegrationHealthWatch:
    """The worker's sweep: every ``task_reconcile_interval_minutes`` reconcile, then evaluate.

    Args:
        reconciler: Reconciles stale tasks.
        health: Evaluates the health state.
        clock: Current time.
        interval: How often (``task_reconcile_interval_minutes``).
    """

    def __init__(
        self,
        reconciler: TaskReconciler,
        health: IntegrationHealthService,
        clock: Clock,
        interval: timedelta | Callable[[], timedelta],
    ) -> None:
        """Store the collaborators; the first sweep is on the first run."""
        self._reconciler = reconciler
        self._health = health
        self._clock = clock
        self._interval = interval
        self._ran_at: datetime | None = None

    async def run(self) -> bool:
        """Reconcile and evaluate when due; return whether it ran."""
        now = self._clock.now()
        if self._ran_at is not None and now - self._ran_at < value_of(self._interval):
            return False
        self._ran_at = now
        await self._reconciler.run()
        await self._health.evaluate()
        return True
