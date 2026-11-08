"""Evaluating connection health and reconciling task states in the worker (ADR 0082).

``IntegrationHealthService`` recomputes the health of every connection from the request events
and writes its transitions; ``TaskReconciler`` asks the contact center about tasks the webhooks
left stale; ``IntegrationHealthWatch`` runs both every ``task_reconcile_interval_minutes``. The
model and its rules are in ``app.domain.connection_health``.
"""

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.calls import EventKind
from app.domain.connection_health import (
    MISSED,
    RECONCILE_LIMIT,
    RECONCILED,
    TASK_EVENTS,
    WEBHOOKS_MISSING,
    HealthEntry,
    HealthPolicy,
    InboundEvent,
    evaluate,
    health_event,
)
from app.domain.ports import Clock, ContactCenterDispatcher, IntegrationError, UnitOfWork
from app.domain.tunable import value_of


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
        """Store ``event`` as its platform's latest verified webhook."""
        async with self._uow.locked() as tx:
            await tx.save_inbound_event(event)

    async def overview(self) -> tuple[tuple[HealthEntry, ...], tuple[InboundEvent, ...]]:
        """Return the stored health of every connection and the last inbound events."""
        async with self._uow.read() as tx:
            return await tx.get_health(), await tx.get_inbound_events()


class TaskReconciler:
    """Reads stale tasks at the contact center and writes what the webhooks missed.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        dispatcher: Reads a task's state (``read_task``); ``None`` without a contact center.
        record: Writes a timeline event and the call status of a booking
            (``IntegrationService.record``), as ``record(booking_id, kind, detail)``.
        after: How long a task may go without a change (``task_reconcile_after_minutes``).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        dispatcher: ContactCenterDispatcher | None,
        record: Callable[[int, EventKind, str], Awaitable[None]],
        after: timedelta | Callable[[], timedelta],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._dispatcher = dispatcher
        self._record = record
        self._after = after

    async def run(self) -> int:
        """Reconcile the stale tasks; return how many states the webhooks had missed.

        A task the contact center no longer knows, or cannot be asked about now, is left as
        it is. Each organisation with a missed state gets one ``integration.webhooks_missing``
        failure with the count; one whose run found none after an open failure gets the
        success that closes the alert.
        """
        if self._dispatcher is None:
            return 0
        now = self._clock.now()
        async with self._uow.read() as tx:
            stale = await tx.stale_tasks(now - value_of(self._after), RECONCILE_LIMIT)
        missed: dict[str | None, int] = {}
        for task in stale:
            try:
                snapshot = await self._dispatcher.read_task(task.task_id)
            except IntegrationError:
                continue
            missed.setdefault(task.organisation, 0)
            kind = TASK_EVENTS.get(snapshot.state)
            if kind is None or snapshot.state is task.state:
                continue
            await self._record(task.booking_id, kind, RECONCILED)
            missed[task.organisation] += 1
        await self._report(missed, now)
        return sum(missed.values())

    async def _report(self, missed: Mapping[str | None, int], now: datetime) -> None:
        """Write the failure per organisation with a miss, and the success that ends one."""
        async with self._uow.locked() as tx:
            open_before = await tx.organisations_missing_webhooks()
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
