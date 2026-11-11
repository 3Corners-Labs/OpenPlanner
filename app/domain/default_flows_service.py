"""Gives every group the legacy pool and flows it lacks (ADR 0121 step 2e).

The writers of a group and of its weekly rules do this themselves; this walk is the repair for
a group that an older image created during a rollback window. It first asks, in a read
transaction, which groups lack something, and only then takes the write lock, once per group and
briefly, so a healthy installation never takes it and one group that fails cannot block the rest.
Idempotent: a second run creates nothing. Counts only, never a name.
"""

from typing import NamedTuple

from app.domain.ports import UnitOfWork


class RepairCounts(NamedTuple):
    """The outcome of a repair walk.

    Attributes:
        repaired: Groups that received at least one pool or flow row.
        failed: Groups whose repair raised; they are left for the next run.
    """

    repaired: int
    failed: int


async def ensure_all_default_flows(uow: UnitOfWork) -> RepairCounts:
    """Repair every group that lacks a pool or default flow, archived ones included.

    A failure of the first (read) step propagates: nothing was changed, and the caller decides.
    A failure for one group is counted and the walk goes on with the next.
    """
    async with uow.read() as tx:
        group_ids = await tx.groups_missing_default_flows()
    repaired = failed = 0
    for group_id in group_ids:
        try:
            async with uow.locked() as tx:
                created = await tx.ensure_default_flows(group_id)
        except Exception:  # one group must not stop the others; the count says it happened
            failed += 1
            continue
        repaired += created
    return RepairCounts(repaired, failed)
