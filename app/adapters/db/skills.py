"""SQL for skills and staff skill grants (ADR 0121 step 2b).

A mixin of ``SqlTransaction``. Skills belong to an organisation; reads filter by ``Scope`` on the
organisation. A viewer whose scope names only a group of the organisation still sees the
organisation's skills, because a service admin refers to them when editing a flow (ADR 0121
decision D2); who may change them is the ``skills.manage`` permission on the route. Every method
that takes an id filters by the scope in SQL, and an id outside it behaves like a missing one.
The rules that SQL cannot express (a skill of another organisation, an archived skill newly added)
are the pure functions of ``app.domain.skills``, called here with the rows already loaded.

The write methods (save, archive, set staff skills) rely on the immediate write lock of
``locked()`` (``BEGIN IMMEDIATE``), so a select-then-insert pre-check such as the code of a skill
cannot interleave with another writer; the unique constraint stays as the backstop. They must not
be called from a ``read()`` transaction, which takes no lock; the unit of work does not stop it.
"""

import json
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.flow_usage import REVISION_IN_EFFECT
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
from app.adapters.db.scope import organisation_is_visible, visible_organisation
from app.domain.authorization import Scope
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.service_flows import FlowState
from app.domain.skills import Skill, SkillUsage, check_skill_save, check_staff_grant
from app.domain.staff import StaffStatus


def skill_from_row(row: SkillRow) -> Skill:
    """Map a skill row."""
    return Skill(row.id, row.organisation_id, row.code, json.loads(row.labels), row.archived_at)


async def load_skills(session: AsyncSession, ids: Collection[int]) -> dict[int, Skill]:
    """Return the skills with these ids by id, whatever their organisation; unknown ids are absent.

    For the rule checks of ``app.domain``, which refuse a skill of another organisation: the
    caller's own scope is applied to the object it writes, not to what it refers to.
    """
    if not ids:
        return {}
    rows = await session.scalars(select(SkillRow).where(SkillRow.id.in_(ids)))
    return {row.id: skill_from_row(row) for row in rows}


class SkillQueries:
    """The skills' and grants' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def _skill_row(self, scope: Scope, skill_id: int) -> SkillRow | None:
        """Return the skill row inside ``scope``, or ``None`` when missing or outside it."""
        return await self._session.scalar(
            select(SkillRow)
            .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
            .where(SkillRow.id == skill_id, visible_organisation(scope, OrganisationRow.slug))
        )

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
        return [skill_from_row(row) for row in await self._session.scalars(query)]

    async def get_skill(self, scope: Scope, skill_id: int) -> Skill | None:
        """Return one skill, or ``None`` when it does not exist or lies outside ``scope``."""
        row = await self._skill_row(scope, skill_id)
        return skill_from_row(row) if row is not None else None

    async def save_skill(self, scope: Scope, skill: Skill) -> Skill:
        """Insert the skill (no id) or update its code and labels; return it with its id.

        Raises:
            DomainError: ``configuration_object_missing`` for an organisation or skill that does
                not exist or lies outside ``scope``, and what ``check_skill_save`` refuses.
        """
        if not await organisation_is_visible(self._session, scope, skill.organisation_id):
            raise missing_object("organisation_id")
        row = None if skill.id is None else await self._skill_row(scope, skill.id)
        taken = await self._session.scalar(
            select(SkillRow.id).where(
                SkillRow.organisation_id == skill.organisation_id,
                SkillRow.code == skill.code,
                SkillRow.id != (skill.id or 0),
            )
        )
        check_skill_save(skill, skill_from_row(row) if row else None, taken is not None)
        if row is None:
            row = SkillRow(organisation_id=skill.organisation_id)
            self._session.add(row)
        row.code = skill.code
        row.labels = json.dumps(dict(skill.labels), sort_keys=True)
        await self._session.flush()
        return skill_from_row(row)

    async def archive_skill(self, scope: Scope, skill_id: int, at: datetime) -> None:
        """Archive a skill; flows, pools and staff that refer to it keep it (A28). Idempotent.

        Raises:
            DomainError: ``configuration_object_missing`` for a skill that does not exist or lies
                outside ``scope``.
        """
        row = await self._skill_row(scope, skill_id)
        if row is None:
            raise missing_object("skill_id")
        if row.archived_at is None:
            row.archived_at = at
            await self._session.flush()

    async def skill_usage(self, scope: Scope, skill_id: int) -> SkillUsage:
        """Return how many staff hold the skill, pools guarantee it and flows require it.

        ``staff`` counts the same grants ``list_staff_skills`` returns: people who are a member of
        the skill's organisation now, once each.
        ``flows`` counts the flows that are not archived, by their current or draft revision, for
        the flow itself or one of its subjects, once each. A skill that does not exist or lies
        outside ``scope`` is used nowhere: all zero.
        """
        if await self._skill_row(scope, skill_id) is None:
            return SkillUsage()
        staff = await self._session.scalar(
            select(func.count(func.distinct(StaffSkillRow.staff_id)))
            .select_from(StaffSkillRow)
            .join(SkillRow, SkillRow.id == StaffSkillRow.skill_id)
            .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
            .join(
                StaffMembershipRow,
                (StaffMembershipRow.staff_id == StaffSkillRow.staff_id)
                & (StaffMembershipRow.organisation == OrganisationRow.slug),
            )
            .where(StaffSkillRow.skill_id == skill_id)
        )
        pools = await self._session.scalar(
            select(func.count()).where(CapacityPoolSkillRow.skill_id == skill_id)
        )
        flows = await self._session.scalar(
            select(func.count(func.distinct(ServiceFlowRow.id)))
            .select_from(ServiceFlowRow)
            .join(FlowRevisionRow, REVISION_IN_EFFECT)
            .outerjoin(
                FlowRevisionSkillRow,
                (FlowRevisionSkillRow.flow_id == FlowRevisionRow.flow_id)
                & (FlowRevisionSkillRow.revision == FlowRevisionRow.revision),
            )
            .outerjoin(
                SubjectFlowSkillRow,
                (SubjectFlowSkillRow.flow_id == FlowRevisionRow.flow_id)
                & (SubjectFlowSkillRow.revision == FlowRevisionRow.revision),
            )
            .where(
                ServiceFlowRow.state != FlowState.ARCHIVED.value,
                or_(
                    FlowRevisionSkillRow.skill_id == skill_id,
                    SubjectFlowSkillRow.skill_id == skill_id,
                ),
            )
        )
        return SkillUsage(staff or 0, pools or 0, flows or 0)

    async def list_staff_skills(self, scope: Scope, staff_id: int) -> frozenset[int]:
        """Return the skill ids the staff member holds, of organisations inside ``scope``.

        Only skills of organisations the person has a membership in *now* count, so a grant never
        outlives the membership for eligibility (the row may stay until they are granted again).
        Someone without a membership in the organisation, a superuser included, therefore holds
        no skills there.
        """
        found = await self._session.scalars(
            select(StaffSkillRow.skill_id)
            .join(SkillRow, SkillRow.id == StaffSkillRow.skill_id)
            .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
            .join(
                StaffMembershipRow,
                (StaffMembershipRow.staff_id == StaffSkillRow.staff_id)
                & (StaffMembershipRow.organisation == OrganisationRow.slug),
            )
            .where(
                StaffSkillRow.staff_id == staff_id,
                visible_organisation(scope, OrganisationRow.slug),
            )
        )
        return frozenset(found)

    async def set_staff_skills(
        self,
        scope: Scope,
        staff_id: int,
        organisation_id: int,
        skill_ids: Collection[int],
        *,
        granted_by: int | None,
        at: datetime,
    ) -> tuple[frozenset[int], frozenset[int]]:
        """Replace the skills the staff member holds *of this organisation*; others stay.

        Returns the ids granted and the ids taken away; every grant of a skill of the
        organisation counts as held, whether or not the person still has a membership in it.

        Removing is always allowed. A skill that is added must be the organisation's own, not
        archived, and the person needs a membership in the organisation; one already held stays
        as it is, whoever granted it and whenever.

        Raises:
            DomainError: ``staff_not_found`` for an unknown or erased staff member,
                ``configuration_object_missing`` for an organisation outside ``scope``, and what
                ``check_staff_grant`` refuses.
        """
        if not await organisation_is_visible(self._session, scope, organisation_id):
            raise missing_object("organisation_id")
        staff = await self._session.get(StaffRow, staff_id)
        if staff is None or staff.status == StaffStatus.ERASED.value:
            raise DomainError(ErrorCode.STAFF_NOT_FOUND)
        member_of = await self._session.scalars(
            select(OrganisationRow.id)
            .join(StaffMembershipRow, StaffMembershipRow.organisation == OrganisationRow.slug)
            .where(StaffMembershipRow.staff_id == staff_id)
        )
        held = set(
            await self._session.scalars(
                select(StaffSkillRow.skill_id)
                .join(SkillRow, SkillRow.id == StaffSkillRow.skill_id)
                .where(
                    StaffSkillRow.staff_id == staff_id, SkillRow.organisation_id == organisation_id
                )
            )
        )
        wanted = frozenset(skill_ids)
        check_staff_grant(
            organisation_id,
            set(member_of),
            await load_skills(self._session, wanted),
            wanted,
            held,
        )
        if held - wanted:
            await self._session.execute(
                delete(StaffSkillRow).where(
                    StaffSkillRow.staff_id == staff_id, StaffSkillRow.skill_id.in_(held - wanted)
                )
            )
        self._session.add_all(
            StaffSkillRow(
                staff_id=staff_id, skill_id=skill_id, granted_at=at, granted_by=granted_by
            )
            for skill_id in sorted(wanted - held)
        )
        await self._session.flush()
        return frozenset(wanted - held), frozenset(held - wanted)
