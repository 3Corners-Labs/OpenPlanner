"""The memory of a service-level alert condition: one event per change (ADR 0061 §7).

An alert is the open streak of a condition event (``app.domain.alerts``). A watch that measures
a condition of each service every loop records an event only when it changes: a failure when it
starts, a success when it ends, so a steady state writes nothing. The memory of which services
are open starts from the open alerts, so a worker that restarts closes an alert whose condition
ended while it was down and does not repeat one that is still open.

    observe(slug, on, detail) ─► changed since last time? ─► system event, memory updated
    keep(slugs)               ─► forget services that are gone, without an event

Used by the queue wait watch (``queue_wait``) and the recovery watch (``request_recovery``).
"""

from collections.abc import Awaitable, Callable, Iterable

from app.domain.audit import Outcome
from app.domain.system_log import SystemLog


class GroupConditions:
    """Which services one condition is open for, and the event of each change.

    Args:
        system_log: Where the events go.
        action: The condition event's action.
        reason: The reason of a failure.
        reported: Returns the slugs with an open alert of ``action``, read at the first use.
    """

    def __init__(
        self,
        system_log: SystemLog,
        action: str,
        reason: str,
        reported: Callable[[], Awaitable[set[str]]],
    ) -> None:
        """Start without memory; the first ``observe`` reads the open alerts."""
        self._system_log = system_log
        self._action = action
        self._reason = reason
        self._reported = reported
        self._open: set[str] | None = None

    async def observe(self, slug: str, on: bool, detail: str) -> bool:
        """Record the event of service ``slug`` if its condition changed; return whether it did."""
        opened = await self._memory()
        if on == (slug in opened):
            return False
        await self._system_log.record(
            self._action,
            Outcome.FAILURE if on else Outcome.SUCCESS,
            group_slug=slug,
            reason=self._reason if on else "",
            detail=detail,
        )
        if on:
            opened.add(slug)
        else:
            opened.discard(slug)
        return True

    async def keep(self, slugs: Iterable[str]) -> None:
        """Forget every service not in ``slugs`` (switched off or erased), without an event."""
        (await self._memory()).intersection_update(slugs)

    async def _memory(self) -> set[str]:
        """Return the open services, read from the open alerts the first time."""
        if self._open is None:
            self._open = await self._reported()
        return self._open
