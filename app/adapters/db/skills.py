"""SQL for skills, staff skill grants and capacity pools (ADR 0121 step 2b).

A mixin of ``SqlTransaction``. Skills and pools belong to an organisation; reads filter by
``Scope`` on the organisation. A viewer whose scope names only a group of the organisation still
sees the organisation's skills and pools, because a service admin refers to them when editing a
flow (ADR 0121 decision D2); changing them is guarded by the ``skills.manage`` and
``capacity.manage`` permissions on the route, never here. What SQL cannot express is enforced in
the writes: a pool guarantees, and a staff member holds, only skills of the organisation, and a
skill is added only while it is not archived (an archived one stays where it already is).
"""

import json
from collections.abc import Collection
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, false, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    CapacityPoolSkillRow,
    FlowRevisionRow,
    FlowRevisionSkillRow,
    OrganisationRow,
    ServiceFlowRow,
    SkillRow,
    StaffMembershipRow,
    StaffRow,
    StaffSkillRow,
    SubjectFlowSkillRow,
)
from app.domain.authorization import Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.skills import Skill, SkillUsage
from app.domain.staff import StaffStatus


def visible_organisation(scope: Scope, slug: Any) -> ColumnElement[bool]:
    """Return the condition that an organisation (by slug column) is visible in ``scope``.

    The organisation itself or any group of it is enough: reading the catalogue is what a
    service admin needs to refer to a skill or a pool.
    """
    if scope.everything:
        return true()
    slugs = sorted(scope.organisations | {organisation for organisation, _ in scope.groups})
    return slug.in_(slugs) if slugs else false()


def _skill(row: SkillRow) -> Skill:
    """Map a skill row."""
    return Skill(row.id, row.organisation_id, row.code, json.loads(row.labels), row.archived_at)


def missing_object(field: str) -> DomainError:
    """Return the error for an object that does not exist."""
    return DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, field)


def outside_organisation(field: str) -> DomainError:
    """Return the error for a reference to another organisation's object."""
    return DomainError(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, field)


class SkillQueries:
    """The skills', grants' and pools' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def list_skills(
        self, scope: Scope, organisation_id: int, *, archived: bool = False
    ) -> list[Skill]:
        """Return the organisation's skills inside ``scope``, by code; archived ones on request."""
        query = (
            select(SkillRow)
            .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
            .where(
                SkillRow.organisation_id == organisation_id,
                visible_organisation(scope, OrganisationRow.slug),
                SkillRow.archived_at.is_not(None) if archived else SkillRow.archived_at.is_(None),
            )
            .order_by(SkillRow.code)
        )
        return [_skill(row) for row in await self._session.scalars(query)]

    async def get_skill(self, scope: Scope, skill_id: int) -> Skill | None:
        """Return one skill, or ``None`` when it does not exist or lies outside ``scope``."""
        row = await self._session.scalar(
            select(SkillRow)
            .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
            .where(SkillRow.id == skill_id, visible_organisation(scope, OrganisationRow.slug))
        )
        return _skill(row) if row is not None else None

    async def save_skill(self, skill: Skill) -> Skill:
        """Insert the skill (no id) or update its code and labels; return it with its id.

        Raises:
            DomainError: ``slug_taken`` (field ``skill_code``) when the organisation has the code
                already, ``configuration_object_missing`` for an unknown organisation or skill,
                ``configuration_out_of_scope`` (field ``organisation_id``) when an update names
                another organisation than the stored skill's.
        """
        row: SkillRow | None = None
        if skill.id is None:
            if await self._session.get(OrganisationRow, skill.organisation_id) is None:
                raise missing_object("organisation_id")
        else:
            row = await self._session.get(SkillRow, skill.id)
            if row is None:
                raise missing_object("skill_id")
            if row.organisation_id != skill.organisation_id:
                raise outside_organisation("organisation_id")
        taken = await self._session.scalar(
            select(SkillRow.id).where(
                SkillRow.organisation_id == skill.organisation_id,
                SkillRow.code == skill.code,
                SkillRow.id != (skill.id or 0),
            )
        )
        if taken is not None:
            raise DomainError(ErrorCode.SLUG_TAKEN, "skill_code")
        if row is None:
            row = SkillRow(organisation_id=skill.organisation_id)
            self._session.add(row)
        row.code = skill.code
        row.labels = json.dumps(dict(skill.labels), sort_keys=True)
        await self._session.flush()
        return _skill(row)

    async def archive_skill(self, skill_id: int, at: datetime) -> None:
        """Archive a skill; flows, pools and staff that refer to it keep it (A28). Idempotent."""
        row = await self._session.get(SkillRow, skill_id)
        if row is None:
            raise missing_object("skill_id")
        if row.archived_at is None:
            row.archived_at = at
            await self._session.flush()

    async def skill_usage(self, skill_id: int) -> SkillUsage:
        """Return how many staff hold the skill, pools guarantee it and flows require it now."""
        staff = await self._session.scalar(
            select(func.count()).where(StaffSkillRow.skill_id == skill_id)
        )
        pools = await self._session.scalar(
            select(func.count()).where(CapacityPoolSkillRow.skill_id == skill_id)
        )
        current = (ServiceFlowRow.id == FlowRevisionRow.flow_id) & (
            ServiceFlowRow.current_revision == FlowRevisionRow.revision
        )
        own = await self._session.scalars(
            select(ServiceFlowRow.id)
            .join(FlowRevisionRow, current)
            .join(
                FlowRevisionSkillRow,
                (FlowRevisionSkillRow.flow_id == FlowRevisionRow.flow_id)
                & (FlowRevisionSkillRow.revision == FlowRevisionRow.revision),
            )
            .where(FlowRevisionSkillRow.skill_id == skill_id)
        )
        extra = await self._session.scalars(
            select(ServiceFlowRow.id)
            .join(FlowRevisionRow, current)
            .join(
                SubjectFlowSkillRow,
                (SubjectFlowSkillRow.flow_id == FlowRevisionRow.flow_id)
                & (SubjectFlowSkillRow.revision == FlowRevisionRow.revision),
            )
            .where(SubjectFlowSkillRow.skill_id == skill_id)
        )
        return SkillUsage(staff or 0, pools or 0, len({*own, *extra}))

    async def list_staff_skills(self, scope: Scope, staff_id: int) -> frozenset[int]:
        """Return the skill ids the staff member holds, of organisations inside ``scope``."""
        found = await self._session.scalars(
            select(StaffSkillRow.skill_id)
            .join(SkillRow, SkillRow.id == StaffSkillRow.skill_id)
            .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
            .where(
                StaffSkillRow.staff_id == staff_id,
                visible_organisation(scope, OrganisationRow.slug),
            )
        )
        return frozenset(found)

    async def staff_with_skill(self, scope: Scope, skill_id: int) -> list[int]:
        """Return the ids of the staff holding the skill; empty outside ``scope``."""
        found = await self._session.scalars(
            select(StaffSkillRow.staff_id)
            .join(SkillRow, SkillRow.id == StaffSkillRow.skill_id)
            .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
            .where(
                StaffSkillRow.skill_id == skill_id,
                visible_organisation(scope, OrganisationRow.slug),
            )
            .order_by(StaffSkillRow.staff_id)
        )
        return list(found)

    async def set_staff_skills(
        self, staff_id: int, skill_ids: Collection[int], *, granted_by: int | None, at: datetime
    ) -> None:
        """Replace the set of skills the staff member holds.

        Removing is always allowed. A skill that is added must exist, not be archived and
        belong to an organisation the staff member has a membership in; one already held stays
        as it is, whoever granted it and whenever.

        Raises:
            DomainError: ``staff_not_found`` for an unknown or erased staff member,
                ``configuration_object_missing`` (field ``skill_ids``) for an unknown skill,
                ``configuration_out_of_scope`` (field ``skill_ids``) for a skill of another
                organisation, ``invalid_configuration`` (field ``skill_ids``) for an archived one.
        """
        staff = await self._session.get(StaffRow, staff_id)
        if staff is None or staff.status == StaffStatus.ERASED.value:
            raise DomainError(ErrorCode.STAFF_NOT_FOUND)
        wanted = frozenset(skill_ids)
        held = set(
            await self._session.scalars(
                select(StaffSkillRow.skill_id).where(StaffSkillRow.staff_id == staff_id)
            )
        )
        added = wanted - held
        if added:
            organisations = set(
                await self._session.scalars(
                    select(StaffMembershipRow.organisation).where(
                        StaffMembershipRow.staff_id == staff_id
                    )
                )
            )
            rows = (
                await self._session.execute(
                    select(SkillRow.id, SkillRow.archived_at, OrganisationRow.slug)
                    .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
                    .where(SkillRow.id.in_(added))
                )
            ).all()
            if len(rows) != len(added):
                raise missing_object("skill_ids")
            if any(slug not in organisations for _, _, slug in rows):
                raise outside_organisation("skill_ids")
            if any(archived_at is not None for _, archived_at, _ in rows):
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "skill_ids")
        for skill_id in held - wanted:
            row = await self._session.get(StaffSkillRow, (staff_id, skill_id))
            if row is not None:
                await self._session.delete(row)
        self._session.add_all(
            StaffSkillRow(
                staff_id=staff_id, skill_id=skill_id, granted_at=at, granted_by=granted_by
            )
            for skill_id in sorted(added)
        )
        await self._session.flush()
