"""Erase archived groups once nothing refers to them any more (ADR 0084 step 3).

Archiving (step 2) keeps the group's row so that its slug stays reserved while past bookings
wait for the purge job and closures run out. The worker runs ``GroupEraser.run`` once a day:
every group archived at least ``after`` ago that no booking, slot, closure, shortfall
acceptance or API client refers to is deleted, and the schema's cascades remove its staffing,
texts, reminders, form fields and settings. The audit log keeps the slug as text; each erase
is a system event ``group.erased`` at the group's level, written before the row goes.
"""

from collections.abc import Callable
from datetime import timedelta

import structlog

from app.domain.audit import Outcome
from app.domain.ports import Clock, UnitOfWork
from app.domain.system_log import SystemLog
from app.domain.tunable import value_of

log = structlog.get_logger()

ERASED = "group.erased"
"""The system event written for every erased group."""


class GroupEraser:
    """Erases archived groups that have waited long enough and are referred to by nothing.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        after: How long after archiving a group may be erased (``group_erase_after_days``),
            or a callable that returns it, so the portal value applies without a restart.
    """

    def __init__(
        self, uow: UnitOfWork, clock: Clock, after: timedelta | Callable[[], timedelta]
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._after = after
        self._events = SystemLog(uow, clock)

    async def run(self) -> int:
        """Erase every group that qualifies; return how many went."""
        before = self._clock.now() - value_of(self._after)
        async with self._uow.read() as tx:
            due = await tx.erasable_groups(before)
        for group in due:
            await self._events.record(ERASED, Outcome.SUCCESS, group_slug=group.slug)
            async with self._uow.locked() as tx:
                await tx.delete_group(group.id)
            log.info("group.erased", group=group.slug)
        return len(due)
