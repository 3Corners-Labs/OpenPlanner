"""In-memory skills, staff grants, capacity pools and service flows (ADR 0121 step 2b).

A mixin of ``InMemoryTransaction``; it behaves like ``app.adapters.db.skills``,
``capacity_pools`` and ``service_flows`` for the cases the domain tests need, including the same
domain errors. Its data lives in the fake's ``State`` (``skills``, ``skill_grants``, ``pools``,
``flows``, ``flow_revisions``).
"""

from collections.abc import Collection
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Group
from app.domain.service_flows import FlowRevision, FlowState, ServiceFlow
from app.domain.skills import Skill, SkillUsage
from app.domain.staff import StaffMember, StaffStatus


def _visible(state: Any, scope: Scope, organisation_id: int) -> bool:
    """Return whether the organisation is visible: itself or any of its groups in ``scope``."""
    slug = state.organisation_slugs.get(organisation_id, "org")
    return (
        scope.everything or slug in scope.organisations or any(o == slug for o, _ in scope.groups)
    )


class ServiceFlowStore:
    """The skills', pools' and flows' part of the in-memory transaction."""

    state: Any

    def _new_id(self) -> int:
        """Return a new id (``InMemoryTransaction``)."""
        raise NotImplementedError

    def _staff(self, staff_id: int) -> StaffMember | None:
        """Return the staff member with row id ``staff_id``."""
        return next((m for m in self.state.staff.values() if m.id == staff_id), None)

    async def list_skills(
        self, scope: Scope, organisation_id: int, *, archived: bool = False
    ) -> list[Skill]:
        found = [
            s
            for s in self.state.skills.values()
            if s.organisation_id == organisation_id
            and s.archived == archived
            and _visible(self.state, scope, s.organisation_id)
        ]
        return sorted(found, key=lambda s: s.code)

    async def get_skill(self, scope: Scope, skill_id: int) -> Skill | None:
        skill = self.state.skills.get(skill_id)
        return skill if skill and _visible(self.state, scope, skill.organisation_id) else None

    async def save_skill(self, skill: Skill) -> Skill:
        if skill.id is None:
            if skill.organisation_id not in self.state.organisation_slugs:
                raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "organisation_id")
        else:
            stored = self.state.skills.get(skill.id)
            if stored is None:
                raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_id")
            if stored.organisation_id != skill.organisation_id:
                raise DomainError(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "organisation_id")
        for other in self.state.skills.values():
            if (other.organisation_id, other.code) == (skill.organisation_id, skill.code) and (
                other.id != skill.id
            ):
                raise DomainError(ErrorCode.SLUG_TAKEN, "skill_code")
        kept = replace(skill, id=skill.id or self._new_id())
        kept = replace(
            kept,
            archived_at=self.state.skills[kept.id].archived_at
            if kept.id in self.state.skills
            else None,
        )
        self.state.skills[kept.id] = kept
        return kept

    async def archive_skill(self, skill_id: int, at: datetime) -> None:
        skill = self.state.skills.get(skill_id)
        if skill is None:
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_id")
        if skill.archived_at is None:
            self.state.skills[skill_id] = replace(skill, archived_at=at)

    async def skill_usage(self, skill_id: int) -> SkillUsage:
        staff = sum(1 for _, skill in self.state.skill_grants if skill == skill_id)
        pools = sum(1 for p in self.state.pools.values() if skill_id in p.guaranteed_skill_ids)
        flows = 0
        for flow in self.state.flows.values():
            current = flow.current
            subject_skills = set().union(*current.subject_skill_ids.values())
            flows += skill_id in current.required_skill_ids or skill_id in subject_skills
        return SkillUsage(staff, pools, flows)

    async def list_staff_skills(self, scope: Scope, staff_id: int) -> frozenset[int]:
        return frozenset(
            skill
            for staff, skill in self.state.skill_grants
            if staff == staff_id
            and _visible(self.state, scope, self.state.skills[skill].organisation_id)
        )

    async def staff_with_skill(self, scope: Scope, skill_id: int) -> list[int]:
        skill = self.state.skills.get(skill_id)
        if skill is None or not _visible(self.state, scope, skill.organisation_id):
            return []
        return sorted(staff for staff, held in self.state.skill_grants if held == skill_id)

    async def set_staff_skills(
        self, staff_id: int, skill_ids: Collection[int], *, granted_by: int | None, at: datetime
    ) -> None:
        member = self._staff(staff_id)
        if member is None or member.status is StaffStatus.ERASED:
            raise DomainError(ErrorCode.STAFF_NOT_FOUND)
        wanted = set(skill_ids)
        held = {skill for staff, skill in self.state.skill_grants if staff == staff_id}
        for skill_id in wanted - held:
            skill = self.state.skills.get(skill_id)
            if skill is None:
                raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_ids")
            slug = self.state.organisation_slugs.get(skill.organisation_id)
            if slug not in {m.organisation for m in member.memberships}:
                raise DomainError(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "skill_ids")
            if skill.archived:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "skill_ids")
        for skill_id in held - wanted:
            del self.state.skill_grants[(staff_id, skill_id)]
        for skill_id in wanted - held:
            self.state.skill_grants[(staff_id, skill_id)] = (granted_by, at)

    async def list_capacity_pools(
        self, scope: Scope, organisation_id: int, *, archived: bool = False
    ) -> list[CapacityPool]:
        found = [
            p
            for p in self.state.pools.values()
            if p.organisation_id == organisation_id
            and (p.archived_at is not None) == archived
            and _visible(self.state, scope, p.organisation_id)
        ]
        return sorted(found, key=lambda p: (p.name, p.id or 0))

    async def get_capacity_pool(self, scope: Scope, pool_id: int) -> CapacityPool | None:
        pool = self.state.pools.get(pool_id)
        return pool if pool and _visible(self.state, scope, pool.organisation_id) else None

    async def save_capacity_pool(self, pool: CapacityPool) -> CapacityPool:
        if (pool.mode is PoolMode.LEGACY_GROUP) != (pool.group_id is not None):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "group_id")
        group: Group | None = self.state.groups.get(pool.group_id) if pool.group_id else None
        if pool.group_id is not None:
            if group is None:
                raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "group_id")
            if group.organisation_id != pool.organisation_id:
                raise DomainError(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "group_id")
            if any(
                p.group_id == pool.group_id and p.id != pool.id for p in self.state.pools.values()
            ):
                raise DomainError(ErrorCode.CONFIGURATION_OBJECT_EXISTS, "group_id")
        stored = self.state.pools.get(pool.id) if pool.id is not None else None
        if pool.id is None:
            if pool.organisation_id not in self.state.organisation_slugs:
                raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "organisation_id")
        elif stored is None:
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id")
        elif stored.organisation_id != pool.organisation_id:
            raise DomainError(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "organisation_id")
        elif (stored.mode, stored.group_id) != (pool.mode, pool.group_id):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "mode")
        held = stored.guaranteed_skill_ids if stored else frozenset()
        for skill_id in pool.guaranteed_skill_ids:
            skill = self.state.skills.get(skill_id)
            if skill is None:
                raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "guaranteed_skill_ids")
            if skill.organisation_id != pool.organisation_id:
                raise DomainError(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "guaranteed_skill_ids")
            if skill.archived and skill_id not in held:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "guaranteed_skill_ids")
        kept = replace(
            pool,
            id=pool.id or self._new_id(),
            archived_at=stored.archived_at if stored else None,
        )
        self.state.pools[kept.id] = kept
        return kept

    async def flows_on_pool(self, pool_id: int) -> list[int]:
        return sorted(
            f.id or 0
            for f in self.state.flows.values()
            if f.current.capacity_pool_id == pool_id and f.state is not FlowState.ARCHIVED
        )

    async def archive_capacity_pool(self, pool_id: int, at: datetime) -> None:
        pool = self.state.pools.get(pool_id)
        if pool is None:
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id")
        if pool.archived_at is not None:
            return
        if any(
            f.state is FlowState.ACTIVE and f.current.capacity_pool_id == pool_id
            for f in self.state.flows.values()
        ):
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "capacity_pool")
        self.state.pools[pool_id] = replace(pool, archived_at=at)

    async def list_service_flows(self, scope: Scope, group_id: int) -> list[ServiceFlow]:
        group = self.state.groups.get(group_id)
        slug = self.state.organisation_slugs.get(group.organisation_id, "org") if group else None
        if group is None or not scope.contains(slug, group.slug):
            return []
        found = [f for f in self.state.flows.values() if f.group_id == group_id]
        return sorted(found, key=lambda f: f.id or 0)

    async def get_service_flow(self, scope: Scope, flow_id: int) -> ServiceFlow | None:
        flow = self.state.flows.get(flow_id)
        if flow is None:
            return None
        return flow if await self.list_service_flows(scope, flow.group_id) else None

    async def flow_revision(self, flow_id: int, revision: int) -> FlowRevision | None:
        return self.state.flow_revisions.get((flow_id, revision))

    def _check_flow(self, organisation_id: int, flow: ServiceFlow, already: set[int]) -> None:
        """Refuse a pool or skill of another organisation, or a skill added while archived."""
        revision = flow.current
        if revision.capacity_pool_id is not None:
            pool = self.state.pools.get(revision.capacity_pool_id)
            if pool is None:
                raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "capacity_pool_id")
            if pool.organisation_id != organisation_id:
                raise DomainError(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "capacity_pool_id")
        for skill_id in set(revision.required_skill_ids).union(
            *revision.subject_skill_ids.values()
        ):
            skill = self.state.skills.get(skill_id)
            if skill is None:
                raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "required_skill_ids")
            if skill.organisation_id != organisation_id:
                raise DomainError(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "required_skill_ids")
            if skill.archived and skill_id not in already:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "required_skill_ids")

    async def save_draft(self, flow: ServiceFlow) -> ServiceFlow:
        group = self.state.groups.get(flow.group_id)
        if group is None:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        revision = flow.current
        if flow.state is not FlowState.DRAFT:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "state")
        if revision.published_at is not None:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "published_at")
        already: set[int] = set()
        if flow.id is None:
            if revision.revision != 1:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "revision")
        else:
            stored = self.state.flows.get(flow.id)
            if stored is None:
                raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id")
            if stored.group_id != flow.group_id:
                raise DomainError(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "group_id")
            if stored.state is not FlowState.DRAFT:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "state")
            if revision.revision != stored.current.revision:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "revision")
            if stored.current.published_at is not None:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "revision_published")
            already = set(stored.current.required_skill_ids).union(
                *stored.current.subject_skill_ids.values()
            )
        if any(
            f.group_id == flow.group_id and f.code == flow.code and f.id != flow.id
            for f in self.state.flows.values()
        ):
            raise DomainError(ErrorCode.SLUG_TAKEN, "flow_code")
        self._check_flow(group.organisation_id, flow, already)
        flow_id = flow.id or self._new_id()
        saved_revision = replace(revision, flow_id=flow_id)
        saved = replace(flow, id=flow_id, current=saved_revision)
        self.state.flows[flow_id] = saved
        self.state.flow_revisions[(flow_id, saved_revision.revision)] = saved_revision
        return saved
