"""Gives every group the legacy pool and flows it lacks (ADR 0121 step 2e).

The writers of a group and of its weekly rules do this themselves; this walk is the repair for
a group that an older image created during a rollback window. Idempotent: a second run creates
nothing. Counts only, never a name.
"""

from app.domain.authorization import Scope
from app.domain.ports import UnitOfWork

EVERYTHING = Scope(everything=True)


async def ensure_all_default_flows(uow: UnitOfWork) -> int:
    """Walk every group, archived ones included, in one write transaction.

    Returns:
        How many groups received at least one pool or flow row.
    """
    async with uow.locked() as tx:
        groups = [
            summary.group
            for archived in (False, True)
            for summary in await tx.list_groups(EVERYTHING, archived=archived)
        ]
        return sum([await tx.ensure_default_flows(group.id) for group in groups])
