"""The senders of the log destinations: queue, batch, back off, report (ADR 0085 point 4).

Every process (web, worker) runs one ``LogShipper``. It is fed by the line tap of
``app.logging`` and keeps one bounded queue and one sender task per enabled destination, so
delivery never slows a request or a job:

    log line ─► tap ─► accepts(destination, record)? ─► queue (drop oldest when full)
                                                           │
                       sender task: batch ─► LogDestinationSender.send ─┬─ ok: counters
                                                                        └─ fail: keep the batch,
                                                                           back off, report

A failing destination is retried with exponential backoff up to ``backoff_max_seconds``. The
state change from working to failing writes one global system event ``logging.destination``
(and one on recovery), at most one failure event per window so a flapping platform does not
fill the audit log. The sender's own problems go to stdout only and are never offered to the
destination that fails, so a broken destination cannot feed itself: the shipper writes no log
line from the delivery path.

The destinations are read from the database every ``RELOAD_SECONDS``; a pipeline is rebuilt
when its destination or its credential changed. Counters and the last reason are stored every
``STATUS_SECONDS`` for the portal; they hold no line, address or answer of the platform.
"""

import asyncio
import json
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Protocol

from app.adapters.db.log_destinations import LogTarget
from app.domain.audit import Outcome
from app.domain.event_schema import rfc3339
from app.domain.log_destinations import (
    HEALTH_EVENT,
    TEST_MESSAGE,
    DeliveryError,
    DestinationStatus,
    LogDestination,
    Reason,
    State,
    accepts,
)
from app.domain.ports import Clock, LogDestinationSender, UnitOfWork
from app.domain.system_log import SystemLog

RELOAD_SECONDS = 15.0
"""How often the destinations are read again."""

STATUS_SECONDS = 30.0
"""How often changed counters are stored for the portal."""

MAX_LINE_BYTES = 65_536
"""A longer line is not queued: one runaway message must not fill a batch."""


@dataclass(frozen=True)
class ShipSettings:
    """The class C settings of ADR 0085 point 4, read again at every use."""

    batch_size: int = 100
    flush_seconds: float = 2
    timeout_seconds: float = 5
    queue_size: int = 10_000
    backoff_max_seconds: float = 300


class TargetSource(Protocol):
    """Where the enabled destinations with their opened credentials come from."""

    async def enabled(self) -> list[LogTarget]:
        """Return the enabled destinations."""
        ...


SenderFactory = Callable[[LogTarget, Callable[[], float]], LogDestinationSender]
"""Builds the sender of a target; the callable returns the current timeout in seconds."""


def probe_line(clock: Clock, service: str, destination: LogDestination) -> str:
    """Return the line *Send test line* delivers: an ``info`` line naming the destination."""
    return json.dumps(
        {
            "@timestamp": rfc3339(clock.now()),
            "log.level": "info",
            "message": TEST_MESSAGE,
            "service.name": service,
            "destination": destination.id,
        },
        separators=(",", ":"),
    )


class Pipeline:
    """The queue and the delivery state of one destination in one process.

    Args:
        target: The destination with its opened credential.
        sender: Delivers a batch; ``None`` when the credential could not be opened.
        service: ``web`` or ``worker``.
        clock: Current time.
        settings: Returns the current ``ShipSettings``.
    """

    def __init__(
        self,
        target: LogTarget,
        sender: LogDestinationSender | None,
        service: str,
        clock: Clock,
        settings: Callable[[], ShipSettings],
    ) -> None:
        """Start idle with an empty queue."""
        self.target = target
        self._sender = sender
        self._service = service
        self._clock = clock
        self._settings = settings
        self._queue: deque[str] = deque()
        self._inflight: list[str] = []
        self._status = DestinationStatus(target.destination.id or 0, service)
        if sender is None:
            self._status = replace(
                self._status, state=State.FAILING, last_error=Reason.NOT_READY.value
            )
        self._wait = 0.0
        self.changed = True

    @property
    def destination(self) -> LogDestination:
        """The destination this pipeline serves."""
        return self.target.destination

    def offer(self, line: str, record: Mapping[str, object]) -> None:
        """Queue the line when the destination accepts it; the oldest line goes when full."""
        if self._sender is None or len(line) > MAX_LINE_BYTES:
            return
        if not accepts(self.destination, record):
            return
        if len(self._queue) >= self._settings().queue_size:
            self._queue.popleft()
            self._status = replace(self._status, dropped=self._status.dropped + 1)
            self.changed = True
        self._queue.append(line)

    def status(self) -> DestinationStatus:
        """Return the counters and state now."""
        return replace(
            self._status,
            queued=len(self._queue) + len(self._inflight),
            updated_at=self._clock.now(),
        )

    def delay(self) -> float:
        """Return how long to wait before the next attempt."""
        if self._wait:
            return self._wait
        if len(self._queue) >= self._settings().batch_size:
            return 0.0
        return self._settings().flush_seconds

    async def flush_once(self) -> State | None:
        """Deliver one batch if there is one; return the state it left, or ``None`` if idle.

        A failed batch is kept and sent again first. The caller reports a change of state.
        """
        if self._sender is None:
            return None
        if not self._inflight:
            size = self._settings().batch_size
            while self._queue and len(self._inflight) < size:
                self._inflight.append(self._queue.popleft())
        if not self._inflight:
            return None
        now = self._clock.now()
        try:
            await self._sender.send(self._inflight)
        except DeliveryError as failed:
            self._wait = min(
                max(1.0, self._wait * 2 if self._wait else 1.0),
                self._settings().backoff_max_seconds,
            )
            self._status = replace(
                self._status,
                state=State.FAILING,
                failed=self._status.failed + 1,
                last_error_at=now,
                last_error=failed.reason.value,
            )
            self.changed = True
            return State.FAILING
        self._status = replace(
            self._status,
            state=State.OK,
            sent=self._status.sent + len(self._inflight),
            last_ok_at=now,
        )
        self._inflight = []
        self._wait = 0.0
        self.changed = True
        return State.OK

    async def aclose(self) -> None:
        """Close the sender."""
        if self._sender is not None:
            await self._sender.aclose()


class LogShipper:
    """All pipelines of one process, fed by the line tap.

    Args:
        source: Gives the enabled destinations with their credentials.
        factory: Builds the sender of a destination.
        uow: Stores the status for the portal.
        clock: Current time.
        service: ``web`` or ``worker``.
        settings: Returns the current ``ShipSettings``.
        enabled: Returns whether shipping is switched on at all (``LOG_SHIPPING_ENABLED``).
        window: Returns the least time between two failure events of one destination.
    """

    def __init__(  # noqa: PLR0913
        self,
        source: TargetSource,
        factory: SenderFactory,
        uow: UnitOfWork,
        clock: Clock,
        service: str,
        settings: Callable[[], ShipSettings] = ShipSettings,
        enabled: Callable[[], bool] = lambda: True,
        window: Callable[[], timedelta] = lambda: timedelta(minutes=15),
    ) -> None:
        """Start without pipelines; ``reload`` creates them."""
        self._source = source
        self._factory = factory
        self._uow = uow
        self._clock = clock
        self._service = service
        self._settings = settings
        self._enabled = enabled
        self._window = window
        self._system = SystemLog(uow, clock)
        self._pipelines: dict[int, Pipeline] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._fingerprints: dict[int, str] = {}
        self._failure_event_at: dict[int, datetime] = {}
        self._reported: set[int] = set()

    @property
    def pipelines(self) -> dict[int, Pipeline]:
        """The running pipelines by destination id."""
        return self._pipelines

    def offer(self, text: str) -> None:
        """The line tap: queue the line for every destination that accepts it."""
        if not self._pipelines or not self._enabled():
            return
        try:
            record = json.loads(text)
        except ValueError:
            return
        if not isinstance(record, dict):
            return
        for pipeline in tuple(self._pipelines.values()):
            pipeline.offer(text, record)

    async def reload(self) -> None:
        """Read the destinations; start, rebuild or stop pipelines to match.

        A failed read (the database is away) keeps the pipelines as they are.
        """
        try:
            targets = await self._source.enabled() if self._enabled() else []
        except Exception:  # the database may be away; the last pipelines stay
            return
        wanted = {t.destination.id: t for t in targets if t.destination.id is not None}
        for destination_id in set(self._pipelines) - set(wanted):
            await self._stop(destination_id)
        for destination_id, target in wanted.items():
            fingerprint = _fingerprint(target)
            if self._fingerprints.get(destination_id) == fingerprint:
                continue
            await self._stop(destination_id)
            self._start(destination_id, target, fingerprint)

    def _start(self, destination_id: int, target: LogTarget, fingerprint: str) -> None:
        """Create the pipeline and its sender task."""
        sender = None if target.unreadable else self._factory(target, self._timeout)
        pipeline = Pipeline(target, sender, self._service, self._clock, self._settings)
        self._pipelines[destination_id] = pipeline
        self._fingerprints[destination_id] = fingerprint
        self._tasks[destination_id] = asyncio.create_task(self._run(destination_id, pipeline))

    async def _stop(self, destination_id: int) -> None:
        """Stop and forget one pipeline."""
        task = self._tasks.pop(destination_id, None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        pipeline = self._pipelines.pop(destination_id, None)
        self._fingerprints.pop(destination_id, None)
        self._reported.discard(destination_id)
        if pipeline is not None:
            await pipeline.aclose()

    def _timeout(self) -> float:
        """The current delivery timeout."""
        return self._settings().timeout_seconds

    async def _run(self, destination_id: int, pipeline: Pipeline) -> None:
        """The sender task of one pipeline: deliver, report changes, wait."""
        while True:
            state = await pipeline.flush_once()
            if state is not None:
                await self._report(destination_id, pipeline, state)
            await asyncio.sleep(pipeline.delay())

    async def _report(self, destination_id: int, pipeline: Pipeline, state: State) -> None:
        """Write the system event of a change from working to failing, and back."""
        destination = pipeline.destination
        detail = f"{destination.kind}:{destination_id}"
        with suppress(Exception):  # an event that cannot be written must not stop delivery
            if state is State.FAILING and destination_id not in self._reported:
                last = self._failure_event_at.get(destination_id)
                now = self._clock.now()
                if last is None or now - last >= self._window():
                    self._failure_event_at[destination_id] = now
                    self._reported.add(destination_id)
                    await self._system.record(
                        HEALTH_EVENT,
                        Outcome.FAILURE,
                        reason=pipeline.status().last_error,
                        detail=detail,
                    )
            elif state is State.OK and destination_id in self._reported:
                self._reported.discard(destination_id)
                await self._system.record(HEALTH_EVENT, Outcome.SUCCESS, detail=detail)

    async def store_status(self) -> None:
        """Store the counters of the pipelines that changed, for the portal."""
        changed = [p for p in self._pipelines.values() if p.changed]
        if not changed:
            return
        try:
            async with self._uow.locked() as tx:
                for pipeline in changed:
                    await tx.save_log_destination_status(pipeline.status(), self._clock.now())
        except Exception:  # the status is a convenience; the database may be away
            return
        for pipeline in changed:
            pipeline.changed = False

    async def run(self, stop: asyncio.Event) -> None:
        """Reload the destinations and store the status until ``stop`` is set."""
        elapsed = STATUS_SECONDS
        while not stop.is_set():
            await self.reload()
            elapsed += RELOAD_SECONDS
            if elapsed >= STATUS_SECONDS:
                await self.store_status()
                elapsed = 0.0
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=RELOAD_SECONDS)
        await self.aclose()

    async def aclose(self) -> None:
        """Stop every pipeline, after a last attempt to deliver what is queued."""
        for pipeline in tuple(self._pipelines.values()):
            await pipeline.flush_once()
        await self.store_status()
        for destination_id in tuple(self._pipelines):
            await self._stop(destination_id)


def _fingerprint(target: LogTarget) -> str:
    """Return a digest of what a sender is built from, so a change rebuilds the pipeline.

    The credential enters the digest, never the output: the digest is kept in memory only.
    """
    d = target.destination
    parts: Sequence[object] = (
        d.kind,
        d.address,
        d.index,
        d.source,
        d.min_level,
        sorted(d.categories),
        d.include_diagnostic,
        d.include_trace,
        d.ca_pem,
        target.secret,
        target.unreadable,
    )
    return sha256(repr(parts).encode()).hexdigest()
