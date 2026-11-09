"""The use case of the live wallboard: who may see it and what it is made from (ADR 0117 step 13).

    authorise ``reports.wallboard.view`` (audited on denial, as every report read)
      ─► the groups in scope and today's hourly cells         (``UsageReportService.load``)
      ─► the live state of the own queues the reader may also work (``queue.work``)
      ─► the places of the slots now running and the next ones
      ─► ``wallboard``  (small cells hidden, the live counts exact)

Nothing is written on this path: the board is a read, so a poll every few seconds only costs
reads, and a screen that is left running cannot fill the audit log or the database. The
live counts are for a reader who may work the queue: another reader gets the board without them.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.errors import DomainError, ErrorCode
from app.domain.own_queue import ParticipantState, participant_state, summarise
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.queue_service import load_entries
from app.domain.scheduling import GroupSummary
from app.domain.usage_report_service import UsageReportService
from app.domain.wallboard import BlockFill, LiveQueue, Wallboard, wallboard

_LOOKAHEAD = timedelta(hours=3)
"""How far ahead the next block is looked for."""


class WallboardService:
    """Builds the wallboard of a group or of an organisation's groups.

    Args:
        uow: Opens read transactions with the groups, slots and bookings.
        usage: Authorises and reads the groups and their hourly cells.
        clock: Current time.
        alert_minutes: ``reporting_wallboard_wait_alert_minutes`` of an organisation id
            (class D, read each time).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        usage: UsageReportService,
        clock: Clock,
        alert_minutes: Callable[[int], int],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._usage = usage
        self._clock = clock
        self._alert = alert_minutes

    async def read(
        self,
        principal: Principal,
        organisation_slug: str | None = None,
        group_slug: str | None = None,
    ) -> tuple[Wallboard, list[GroupSummary]]:
        """Return the board and the groups it covers, inside ``reports.wallboard.view``.

        Raises:
            DomainError: ``not_permitted`` (audited), ``group_not_found``.
        """
        now = self._clock.now()
        loaded = await self._usage.load(
            principal,
            organisation_slug,
            group_slug,
            now.date(),
            now.date(),
            Permission.REPORTS_WALLBOARD_VIEW,
        )
        slugs = {g.slug for g in loaded.groups}
        board_scope = scope_for(principal, Permission.REPORTS_WALLBOARD_VIEW)
        queue_scope = scope_for(principal, Permission.QUEUE_WORK)
        async with self._uow.read() as tx:
            organisation = loaded.groups[0].organisation_slug
            covered = [
                s
                for s in await tx.list_groups(board_scope)
                if s.group.slug in slugs and s.organisation_slug == organisation
            ]
            if not covered:
                raise DomainError(ErrorCode.GROUP_NOT_FOUND)
            live_scope = {
                s.group.slug
                for s in await tx.list_groups(queue_scope)
                if s.organisation_slug == organisation
            }
            queues = [
                await _queue_of(tx, queue_scope, s, now)
                for s in covered
                if s.group.queue_open and s.group.slug in live_scope
            ]
            now_fill, next_fill = await _blocks(tx, covered, now)
        board = wallboard(
            loaded.groups,
            loaded.cells,
            queues,
            now_fill,
            next_fill,
            now,
            loaded.threshold,
            self._alert(loaded.groups[0].organisation_id),
            loaded.as_of,
        )
        return board, covered


async def _queue_of(
    tx: Transaction, scope: Scope, summary: GroupSummary, now: datetime
) -> LiveQueue:
    """Return one group's own queue now: who waits, how long at most, who is in a call."""
    entries = await load_entries(tx, scope, summary.group, now)
    queue = summarise(entries, now)
    connected = sum(1 for e in entries if participant_state(e.status) is ParticipantState.IN_CALL)
    return LiveQueue(queue.waiting, queue.longest_wait, connected)


async def _blocks(
    tx: Transaction, groups: list[GroupSummary], now: datetime
) -> tuple[BlockFill | None, BlockFill | None]:
    """Return the places of the slots now running and of the next ones, over ``groups``."""
    running = [0, 0, 0]
    upcoming = [0, 0, 0]
    for summary in groups:
        length = timedelta(minutes=summary.group.slot_minutes)
        slots = await tx.get_slot_usage(summary.group.id or 0, now - length, now + _LOOKAHEAD)
        here = [s for s in slots if s.start <= now < s.start + length]
        later = sorted((s for s in slots if s.start > now), key=lambda s: s.start)[:1]
        for found, total in ((here, running), (later, upcoming)):
            for slot in found:
                total[0] += slot.active
                total[1] += slot.capacity_snapshot
                total[2] += 1
    return (
        BlockFill(running[0], running[1]) if running[2] else None,
        BlockFill(upcoming[0], upcoming[1]) if upcoming[2] else None,
    )
