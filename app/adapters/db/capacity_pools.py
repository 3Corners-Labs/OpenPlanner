"""SQL for capacity pools (ADR 0121 step 2b).

A mixin of ``SqlTransaction``. Pools belong to an organisation and are read with the same
visibility as skills (``visible_organisation``). A pool guarantees only skills of its own
organisation, a skill is added to the guarantee only while it is not archived, and a pool with an
active flow cannot be archived. The mode and group of a pool never change.
"""

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    CapacityPoolRow,
    CapacityPoolSkillRow,
    FlowRevisionRow,
    GroupRow,
    OrganisationRow,
    ServiceFlowRow,
    SkillRow,
)
from app.adapters.db.skills import missing_object, outside_organisation, visible_organisation
from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.errors import DomainError, ErrorCode


class CapacityPoolQueries:
    """The capacity pools' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def _pools(self, rows: Collection[CapacityPoolRow]) -> list[CapacityPool]:
        """Map pool rows with the skills each guarantees."""
        guaranteed: dict[int, set[int]] = {row.id: set() for row in rows}
        if guaranteed:
            for pool_id, skill_id in (
                await self._session.execute(
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
        return await self._pools(list(rows))

    async def get_capacity_pool(self, scope: Scope, pool_id: int) -> CapacityPool | None:
        """Return one pool, or ``None`` when it does not exist or lies outside ``scope``."""
        row = await self._session.scalar(
            select(CapacityPoolRow)
            .join(OrganisationRow, OrganisationRow.id == CapacityPoolRow.organisation_id)
            .where(CapacityPoolRow.id == pool_id, visible_organisation(scope, OrganisationRow.slug))
        )
        return (await self._pools([row]))[0] if row is not None else None

    async def _check_pool_group(self, pool: CapacityPool) -> None:
        """Refuse a legacy group pool without a group of its organisation, or a counted one with."""
        if (pool.mode is PoolMode.LEGACY_GROUP) != (pool.group_id is not None):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "group_id")
        if pool.group_id is None:
            return
        group = await self._session.get(GroupRow, pool.group_id)
        if group is None:
            raise missing_object("group_id")
        if group.organisation_id != pool.organisation_id:
            raise outside_organisation("group_id")
        other = await self._session.scalar(
            select(CapacityPoolRow.id).where(
                CapacityPoolRow.group_id == pool.group_id, CapacityPoolRow.id != (pool.id or 0)
            )
        )
        if other is not None:
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_EXISTS, "group_id")

    async def _check_guaranteed(self, pool: CapacityPool, held: set[int]) -> None:
        """Refuse a guaranteed skill that is unknown, another organisation's or newly archived."""
        rows = (
            await self._session.execute(
                select(SkillRow.id, SkillRow.organisation_id, SkillRow.archived_at).where(
                    SkillRow.id.in_(pool.guaranteed_skill_ids)
                )
            )
        ).all()
        if len(rows) != len(pool.guaranteed_skill_ids):
            raise missing_object("guaranteed_skill_ids")
        if any(organisation != pool.organisation_id for _, organisation, _ in rows):
            raise outside_organisation("guaranteed_skill_ids")
        if any(archived is not None and skill_id not in held for skill_id, _, archived in rows):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "guaranteed_skill_ids")

    async def save_capacity_pool(self, pool: CapacityPool) -> CapacityPool:
        """Insert the pool (no id) or update its name and guaranteed skills; return it stored.

        A pool's mode and group never change. Skills must be the organisation's own, and only
        a skill the pool already guarantees may be archived.

        Raises:
            DomainError: ``invalid_configuration`` (fields ``group_id``, ``mode``,
                ``guaranteed_skill_ids``), ``configuration_object_missing``,
                ``configuration_out_of_scope`` or ``configuration_object_exists`` (field
                ``group_id``: the group has a pool already).
        """
        await self._check_pool_group(pool)
        row: CapacityPoolRow | None
        held: set[int] = set()
        if pool.id is None:
            if await self._session.get(OrganisationRow, pool.organisation_id) is None:
                raise missing_object("organisation_id")
            row = CapacityPoolRow(
                organisation_id=pool.organisation_id, mode=pool.mode.value, group_id=pool.group_id
            )
        else:
            row = await self._session.get(CapacityPoolRow, pool.id)
            if row is None:
                raise missing_object("pool_id")
            if row.organisation_id != pool.organisation_id:
                raise outside_organisation("organisation_id")
            if (row.mode, row.group_id) != (pool.mode.value, pool.group_id):
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "mode")
            held = set(
                await self._session.scalars(
                    select(CapacityPoolSkillRow.skill_id).where(
                        CapacityPoolSkillRow.pool_id == pool.id
                    )
                )
            )
        await self._check_guaranteed(pool, held)
        row.name = pool.name
        self._session.add(row)
        await self._session.flush()
        for skill_id in held - pool.guaranteed_skill_ids:
            gone = await self._session.get(CapacityPoolSkillRow, (row.id, skill_id))
            if gone is not None:
                await self._session.delete(gone)
        self._session.add_all(
            CapacityPoolSkillRow(pool_id=row.id, skill_id=skill_id)
            for skill_id in sorted(pool.guaranteed_skill_ids - held)
        )
        await self._session.flush()
        return (await self._pools([row]))[0]

    async def flows_on_pool(self, pool_id: int) -> list[int]:
        """Return the ids of the flows, not archived, whose current revision draws on the pool."""
        found = await self._session.scalars(
            select(ServiceFlowRow.id)
            .join(
                FlowRevisionRow,
                (FlowRevisionRow.flow_id == ServiceFlowRow.id)
                & (FlowRevisionRow.revision == ServiceFlowRow.current_revision),
            )
            .where(FlowRevisionRow.capacity_pool_id == pool_id, ServiceFlowRow.state != "archived")
            .order_by(ServiceFlowRow.id)
        )
        return list(found)

    async def archive_capacity_pool(self, pool_id: int, at: datetime) -> None:
        """Archive a pool; refused while an active flow's current revision draws on it.

        Raises:
            DomainError: ``configuration_object_missing`` or ``configuration_object_in_use``
                (field ``capacity_pool``).
        """
        row = await self._session.get(CapacityPoolRow, pool_id)
        if row is None:
            raise missing_object("pool_id")
        if row.archived_at is not None:
            return
        active = await self._session.scalar(
            select(ServiceFlowRow.id)
            .join(
                FlowRevisionRow,
                (FlowRevisionRow.flow_id == ServiceFlowRow.id)
                & (FlowRevisionRow.revision == ServiceFlowRow.current_revision),
            )
            .where(FlowRevisionRow.capacity_pool_id == pool_id, ServiceFlowRow.state == "active")
            .limit(1)
        )
        if active is not None:
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "capacity_pool")
        row.archived_at = at
        await self._session.flush()
