"""SQL for capacity pools (ADR 0121 step 2b).

A mixin of ``SqlTransaction``. Pools belong to an organisation and are read with the same
visibility as skills (``visible_organisation``); every method that takes an id filters by the
``Scope`` in SQL, and an id outside it behaves like a missing one. What SQL cannot express (a skill
of another organisation, a group of another organisation, a pool in use) is the pure functions of
``app.domain.capacity``, called here with the rows already loaded. Pools are archived, never
deleted. The write methods (save, archive) rely on the immediate write lock of ``locked()``
(``BEGIN IMMEDIATE``), so the pre-check that a group has one legacy pool cannot interleave with
another writer; the unique constraint is the backstop. They must not be called from a ``read()``
transaction, which takes no lock; the unit of work does not stop it.
"""

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.flow_usage import REVISION_IN_EFFECT
from app.adapters.db.models import (
    CapacityPoolRow,
    CapacityPoolSkillRow,
    FlowRevisionRow,
    GroupRow,
    OrganisationRow,
    ServiceFlowRow,
)
from app.adapters.db.scope import organisation_is_visible, scope_filter, visible_organisation
from app.adapters.db.skills import load_skills
from app.domain.authorization import Scope
from app.domain.capacity import (
    CapacityPool,
    PoolFlows,
    PoolMode,
    check_pool_archive,
    check_pool_save,
)
from app.domain.errors import missing_object
from app.domain.service_flows import FlowState


async def pools_from_rows(
    session: AsyncSession, rows: Collection[CapacityPoolRow]
) -> list[CapacityPool]:
    """Map pool rows with the skills each guarantees."""
    guaranteed: dict[int, set[int]] = {row.id: set() for row in rows}
    if guaranteed:
        for pool_id, skill_id in (
            await session.execute(
                select(CapacityPoolSkillRow.pool_id, CapacityPoolSkillRow.skill_id).where(
                    CapacityPoolSkillRow.pool_id.in_(guaranteed)
                )
            )
        ).all():
            guaranteed[pool_id].add(skill_id)
    return [
        CapacityPool(
            row.id,
            row.organisation_id,
            row.name,
            PoolMode(row.mode),
            frozenset(guaranteed[row.id]),
            row.archived_at,
            row.group_id,
        )
        for row in rows
    ]


class CapacityPoolQueries:
    """The capacity pools' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def _pool_row(self, scope: Scope, pool_id: int) -> CapacityPoolRow | None:
        """Return the pool row inside ``scope``, or ``None`` when missing or outside it."""
        return await self._session.scalar(
            select(CapacityPoolRow)
            .join(OrganisationRow, OrganisationRow.id == CapacityPoolRow.organisation_id)
            .where(CapacityPoolRow.id == pool_id, visible_organisation(scope, OrganisationRow.slug))
        )

    async def list_capacity_pools(
        self, scope: Scope, organisation_id: int, *, archived: bool = False
    ) -> list[CapacityPool]:
        """Return the organisation's pools inside ``scope``, by name; archived ones on request."""
        rows = await self._session.scalars(
            select(CapacityPoolRow)
            .join(OrganisationRow, OrganisationRow.id == CapacityPoolRow.organisation_id)
            .where(
                CapacityPoolRow.organisation_id == organisation_id,
                visible_organisation(scope, OrganisationRow.slug),
                CapacityPoolRow.archived_at.is_not(None)
                if archived
                else CapacityPoolRow.archived_at.is_(None),
            )
            .order_by(CapacityPoolRow.name, CapacityPoolRow.id)
        )
        return await pools_from_rows(self._session, list(rows))

    async def get_capacity_pool(self, scope: Scope, pool_id: int) -> CapacityPool | None:
        """Return one pool, or ``None`` when it does not exist or lies outside ``scope``."""
        row = await self._pool_row(scope, pool_id)
        return (await pools_from_rows(self._session, [row]))[0] if row is not None else None

    async def save_capacity_pool(self, scope: Scope, pool: CapacityPool) -> CapacityPool:
        """Insert the pool (no id) or update its name and guaranteed skills; return it stored.

        A pool's mode and group never change.

        Raises:
            DomainError: ``configuration_object_missing`` for an organisation or pool that does
                not exist or lies outside ``scope``, and what ``check_pool_save`` refuses.
        """
        if not await organisation_is_visible(self._session, scope, pool.organisation_id):
            raise missing_object("organisation_id")
        row = None if pool.id is None else await self._pool_row(scope, pool.id)
        stored = (await pools_from_rows(self._session, [row]))[0] if row is not None else None
        group_organisation = (
            None
            if pool.group_id is None
            else await self._session.scalar(
                select(GroupRow.organisation_id).where(GroupRow.id == pool.group_id)
            )
        )
        group_taken = pool.group_id is not None and (
            await self._session.scalar(
                select(CapacityPoolRow.id).where(
                    CapacityPoolRow.group_id == pool.group_id, CapacityPoolRow.id != (pool.id or 0)
                )
            )
            is not None
        )
        held = stored.guaranteed_skill_ids if stored else frozenset[int]()
        check_pool_save(
            pool,
            stored,
            group_organisation,
            await load_skills(self._session, pool.guaranteed_skill_ids),
            held,
            group_taken=group_taken,
        )
        if row is None:
            row = CapacityPoolRow(
                organisation_id=pool.organisation_id, mode=pool.mode.value, group_id=pool.group_id
            )
        row.name = pool.name
        self._session.add(row)
        await self._session.flush()
        if held - pool.guaranteed_skill_ids:
            await self._session.execute(
                delete(CapacityPoolSkillRow).where(
                    CapacityPoolSkillRow.pool_id == row.id,
                    CapacityPoolSkillRow.skill_id.in_(held - pool.guaranteed_skill_ids),
                )
            )
        self._session.add_all(
            CapacityPoolSkillRow(pool_id=row.id, skill_id=skill_id)
            for skill_id in sorted(pool.guaranteed_skill_ids - held)
        )
        await self._session.flush()
        return (await pools_from_rows(self._session, [row]))[0]

    async def flows_on_pool(self, scope: Scope, pool_id: int) -> PoolFlows:
        """Return the flows, not archived, that draw on the pool, by id, as far as ``scope`` sees.

        A flow draws on the pool by its current or its draft revision. Flows of groups outside
        ``scope`` are not listed but counted in ``hidden``. A pool that does not exist or lies
        outside ``scope`` has no flows.
        """
        if await self._pool_row(scope, pool_id) is None:
            return PoolFlows()
        drawing = (
            FlowRevisionRow.capacity_pool_id == pool_id,
            ServiceFlowRow.state != FlowState.ARCHIVED.value,
        )
        found = await self._session.scalars(
            select(ServiceFlowRow.id)
            .join(FlowRevisionRow, REVISION_IN_EFFECT)
            .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(*drawing, scope_filter(scope, OrganisationRow.slug, GroupRow.slug))
            .distinct()
            .order_by(ServiceFlowRow.id)
        )
        seen = tuple(found)
        total = await self._session.scalar(
            select(func.count(func.distinct(ServiceFlowRow.id)))
            .select_from(ServiceFlowRow)
            .join(FlowRevisionRow, REVISION_IN_EFFECT)
            .where(*drawing)
        )
        return PoolFlows(seen, (total or 0) - len(seen))

    async def archive_capacity_pool(self, scope: Scope, pool_id: int, at: datetime) -> None:
        """Archive a pool; refused while a flow not archived draws on it, by current or draft.

        Every flow counts, whatever the scope of the caller: a pool must not become unusable
        under a group the caller cannot see.

        Raises:
            DomainError: ``configuration_object_missing`` for a pool that does not exist or lies
                outside ``scope``, ``configuration_object_in_use`` (field ``capacity_pool``).
        """
        row = await self._pool_row(scope, pool_id)
        if row is None:
            raise missing_object("pool_id")
        if row.archived_at is not None:
            return
        drawing = await self._session.scalars(
            select(ServiceFlowRow.code)
            .join(FlowRevisionRow, REVISION_IN_EFFECT)
            .where(
                FlowRevisionRow.capacity_pool_id == pool_id,
                ServiceFlowRow.state != FlowState.ARCHIVED.value,
            )
            .distinct()
        )
        check_pool_archive(list(drawing))
        row.archived_at = at
        await self._session.flush()
