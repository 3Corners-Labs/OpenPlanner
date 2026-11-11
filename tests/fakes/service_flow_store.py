"""In-memory skills, staff grants, capacity pools and service flows (ADR 0121 step 2b).

A mixin of ``InMemoryTransaction``. It only *stores*: every rule is the pure function of
``app.domain.skills``, ``capacity`` and ``service_flows`` that the SQL adapters call too, so the
fake cannot disagree with them about a rule; what it keeps in common with the adapters is storage
and scope, pinned by the contract tests. Its data lives in the fake's ``State`` (``skills``,
``skill_grants``, ``pools``, ``flows``, ``flow_revisions``).
"""

from collections.abc import Collection
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolFlows, check_pool_archive, check_pool_save
from app.domain.default_flows import (
    bind_pool,
    default_flow_set,
    missing_defaults,
    offers_direct,
    stored_flow,
)
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.scheduling import Group
from app.domain.service_flows import (
    FlowRevision,
    FlowState,
    IntakeMode,
    ServiceFlow,
    check_default_paths,
    check_draft_save,
    check_flow_references,
    check_publishable,
    check_state_change,
)
from app.domain.skills import Skill, SkillUsage, check_skill_save, check_staff_grant
from app.domain.staff import StaffMember, StaffStatus
from tests.support.domain import NOW


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

    def _holds(self, staff_id: int, skill_id: int) -> bool:
        """Return whether the person is a member of the skill's organisation, so a grant counts."""
        member = self._staff(staff_id)
        slug = self.state.organisation_slugs.get(self.state.skills[skill_id].organisation_id)
        return member is not None and any(m.organisation == slug for m in member.memberships)

    def _check_references(
        self, group: Group, revision: FlowRevision, based_on: FlowRevision | None
    ) -> None:
        """Run ``check_flow_references`` against the present pool, skills and subjects."""
        pool = (
            self.state.pools.get(revision.capacity_pool_id) if revision.capacity_pool_id else None
        )
        check_flow_references(
            group.organisation_id,
            revision,
            pool,
            {s: self.state.skills[s] for s in revision.all_skill_ids if s in self.state.skills},
            based_on.all_skill_ids if based_on else frozenset(),
            [subject.code for subject in group.routing.subjects],
            already_pool_id=based_on.capacity_pool_id if based_on else None,
        )

    def _put_flow(self, flow: ServiceFlow) -> None:
        """Store a flow and index its revisions."""
        assert flow.id is not None
        self.state.flows[flow.id] = flow
        for revision in (flow.current, flow.draft):
            if revision is not None:
                self.state.flow_revisions[(flow.id, revision.revision)] = revision

    def forget_staff(self, staff_id: int) -> None:
        """Erase a person's skills and every trace of their id (what ``erase_staff`` does)."""
        for grant in [g for g in self.state.skill_grants if g[0] == staff_id]:
            del self.state.skill_grants[grant]
        for key, (by, at) in list(self.state.skill_grants.items()):
            if by == staff_id:
                self.state.skill_grants[key] = (None, at)
        for flow in list(self.state.flows.values()):
            revisions = [
                replace(r, published_by=None) if r and r.published_by == staff_id else r
                for r in (flow.current, flow.draft)
            ]
            self._put_flow(replace(flow, current=revisions[0], draft=revisions[1]))
        for key, revision in list(self.state.flow_revisions.items()):
            if revision.published_by == staff_id:
                self.state.flow_revisions[key] = replace(revision, published_by=None)

    def _scoped_flow(self, scope: Scope, flow_id: int) -> ServiceFlow | None:
        """Return the flow when its group is inside ``scope``."""
        flow = self.state.flows.get(flow_id)
        group = self.state.groups.get(flow.group_id) if flow else None
        slug = self.state.organisation_slugs.get(group.organisation_id, "org") if group else None
        return flow if group and scope.contains(slug, group.slug) else None

    def _visible_organisation(self, scope: Scope, organisation_id: int) -> bool:
        """Return whether the organisation exists and is visible in ``scope``."""
        return organisation_id in self.state.organisation_slugs and _visible(
            self.state, scope, organisation_id
        )

    async def ensure_default_flows(self, group_id: int) -> bool:
        group = self.state.groups.get(group_id)
        if group is None:
            return False
        wanted = default_flow_set(
            group_id,
            group.organisation_id,
            group.name,
            group.slot_minutes,
            group.dispatch_kind,
            offers_direct(self.state.rules.get(group_id, [])),
            group.archived_at is not None,
        )
        pool = next((p for p in self.state.pools.values() if p.group_id == group_id), None)
        flows = [f for f in self.state.flows.values() if f.group_id == group_id]
        missing = missing_defaults(wanted, pool is not None, [stored_flow(f) for f in flows])
        if missing.empty:
            return False
        if missing.pool is not None:
            pool = replace(missing.pool, id=self._new_id())
            self.state.pools[pool.id] = pool
        assert pool is not None
        assert pool.id is not None
        for flow in bind_pool(missing, pool.id).flows:
            assert flow.current is not None
            flow_id = self._new_id()
            current = replace(flow.current, flow_id=flow_id, published_at=NOW, published_by=None)
            self._put_flow(replace(flow, id=flow_id, current=current))
        return True

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

    async def save_skill(self, scope: Scope, skill: Skill) -> Skill:
        if not self._visible_organisation(scope, skill.organisation_id):
            raise missing_object("organisation_id")
        stored = None if skill.id is None else await self.get_skill(scope, skill.id)
        taken = any(
            (o.organisation_id, o.code) == (skill.organisation_id, skill.code) and o.id != skill.id
            for o in self.state.skills.values()
        )
        check_skill_save(skill, stored, taken)
        kept = replace(skill, id=skill.id or self._new_id(), archived_at=None)
        if stored is not None:
            kept = replace(kept, archived_at=stored.archived_at)
        self.state.skills[kept.id] = kept
        return kept

    async def archive_skill(self, scope: Scope, skill_id: int, at: datetime) -> None:
        skill = await self.get_skill(scope, skill_id)
        if skill is None:
            raise missing_object("skill_id")
        if skill.archived_at is None:
            self.state.skills[skill_id] = replace(skill, archived_at=at)

    async def skill_usage(self, scope: Scope, skill_id: int) -> SkillUsage:
        if await self.get_skill(scope, skill_id) is None:
            return SkillUsage()
        staff = sum(
            1
            for who, skill in self.state.skill_grants
            if skill == skill_id and self._holds(who, skill)
        )
        pools = sum(1 for p in self.state.pools.values() if skill_id in p.guaranteed_skill_ids)
        flows = sum(
            any(skill_id in r.all_skill_ids for r in (flow.current, flow.draft) if r)
            for flow in self.state.flows.values()
            if flow.state is not FlowState.ARCHIVED
        )
        return SkillUsage(staff, pools, flows)

    async def list_staff_skills(self, scope: Scope, staff_id: int) -> frozenset[int]:
        return frozenset(
            skill
            for staff, skill in self.state.skill_grants
            if staff == staff_id
            and _visible(self.state, scope, self.state.skills[skill].organisation_id)
            and self._holds(staff, skill)
        )

    async def set_staff_skills(
        self,
        scope: Scope,
        staff_id: int,
        organisation_id: int,
        skill_ids: Collection[int],
        *,
        granted_by: int | None,
        at: datetime,
    ) -> None:
        if not self._visible_organisation(scope, organisation_id):
            raise missing_object("organisation_id")
        member = self._staff(staff_id)
        if member is None or member.status is StaffStatus.ERASED:
            raise DomainError(ErrorCode.STAFF_NOT_FOUND)
        slugs = self.state.organisation_slugs
        places = {m.organisation for m in member.memberships}
        member_of = {o for o, slug in slugs.items() if slug in places}
        held = {
            skill
            for staff, skill in self.state.skill_grants
            if staff == staff_id and self.state.skills[skill].organisation_id == organisation_id
        }
        wanted = frozenset(skill_ids)
        check_staff_grant(
            organisation_id,
            member_of,
            {s: self.state.skills[s] for s in wanted if s in self.state.skills},
            wanted,
            held,
        )
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

    async def save_capacity_pool(self, scope: Scope, pool: CapacityPool) -> CapacityPool:
        if not self._visible_organisation(scope, pool.organisation_id):
            raise missing_object("organisation_id")
        stored = None if pool.id is None else await self.get_capacity_pool(scope, pool.id)
        group: Group | None = self.state.groups.get(pool.group_id) if pool.group_id else None
        taken = any(
            p.group_id == pool.group_id and p.id != pool.id
            for p in self.state.pools.values()
            if pool.group_id is not None
        )
        held = stored.guaranteed_skill_ids if stored else frozenset[int]()
        check_pool_save(
            pool,
            stored,
            group.organisation_id if group else None,
            {s: self.state.skills[s] for s in pool.guaranteed_skill_ids if s in self.state.skills},
            held,
            group_taken=taken,
        )
        kept = replace(
            pool, id=pool.id or self._new_id(), archived_at=stored.archived_at if stored else None
        )
        self.state.pools[kept.id] = kept
        return kept

    def _drawing_on(self, pool_id: int) -> list[ServiceFlow]:
        """Return the flows not archived whose current or draft revision names the pool."""
        return sorted(
            (
                f
                for f in self.state.flows.values()
                if f.state is not FlowState.ARCHIVED
                and pool_id in {r.capacity_pool_id for r in (f.current, f.draft) if r}
            ),
            key=lambda f: f.id or 0,
        )

    async def flows_on_pool(self, scope: Scope, pool_id: int) -> PoolFlows:
        if await self.get_capacity_pool(scope, pool_id) is None:
            return PoolFlows()
        drawing = self._drawing_on(pool_id)
        seen = tuple(f.id or 0 for f in drawing if self._scoped_flow(scope, f.id or 0))
        return PoolFlows(seen, len(drawing) - len(seen))

    async def archive_capacity_pool(self, scope: Scope, pool_id: int, at: datetime) -> None:
        pool = await self.get_capacity_pool(scope, pool_id)
        if pool is None:
            raise missing_object("pool_id")
        if pool.archived_at is not None:
            return
        check_pool_archive([f.code for f in self._drawing_on(pool_id)])
        self.state.pools[pool_id] = replace(pool, archived_at=at)

    async def list_service_flows(self, scope: Scope, group_id: int) -> list[ServiceFlow]:
        found = [f for f in self.state.flows.values() if f.group_id == group_id]
        return sorted(
            (f for f in found if self._scoped_flow(scope, f.id or 0)), key=lambda f: f.id or 0
        )

    async def get_service_flow(self, scope: Scope, flow_id: int) -> ServiceFlow | None:
        return self._scoped_flow(scope, flow_id)

    async def flow_revision(self, flow_id: int, revision: int) -> FlowRevision | None:
        return self.state.flow_revisions.get((flow_id, revision))

    async def save_draft(self, scope: Scope, flow: ServiceFlow) -> ServiceFlow:
        group = self.state.groups.get(flow.group_id)
        slug = self.state.organisation_slugs.get(group.organisation_id, "org") if group else None
        if group is None or not scope.contains(slug, group.slug):
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        stored = None if flow.id is None else self._scoped_flow(scope, flow.id)
        taken = any(
            f.group_id == flow.group_id and f.code == flow.code and f.id != flow.id
            for f in self.state.flows.values()
        )
        draft = check_draft_save(flow, stored, taken)
        based_on = (stored.draft or stored.current) if stored else None
        self._check_references(group, draft, based_on)
        flow_id = flow.id or self._new_id()
        if stored is not None and stored.draft is not None:
            number = stored.draft.revision
        else:
            number = (stored.current.revision if stored and stored.current else 0) + 1
        draft = replace(draft, flow_id=flow_id, revision=number, published_at=None)
        if stored is None:  # a new flow: always a draft, whatever state the caller passed
            saved = ServiceFlow(
                flow_id, flow.group_id, flow.code, FlowState.DRAFT, flow.default_for, None, draft
            )
        elif stored.current is None:  # never published: code and defaults may still change
            saved = replace(stored, code=flow.code, default_for=flow.default_for, draft=draft)
        else:
            saved = replace(stored, draft=draft)
        self._put_flow(saved)
        return saved

    async def publish_draft(
        self, scope: Scope, flow_id: int, *, by: int, at: datetime
    ) -> ServiceFlow:
        flow = self._scoped_flow(scope, flow_id)
        if flow is None:
            raise missing_object("flow_id")
        draft = check_publishable(flow)
        self._check_references(self.state.groups[flow.group_id], draft, flow.current)
        published = replace(draft, published_at=at, published_by=by)
        saved = replace(flow, current=published, draft=None)
        self._put_flow(saved)
        return saved

    async def discard_draft(self, scope: Scope, flow_id: int) -> None:
        flow = self._scoped_flow(scope, flow_id)
        if flow is None:
            raise missing_object("flow_id")
        if flow.draft is None:
            raise missing_object("draft")
        self.state.flow_revisions.pop((flow_id, flow.draft.revision), None)
        if flow.current is None:
            del self.state.flows[flow_id]
        else:
            self.state.flows[flow_id] = replace(flow, draft=None)

    async def set_flow_state(self, scope: Scope, flow_id: int, state: FlowState) -> ServiceFlow:
        flow = self._scoped_flow(scope, flow_id)
        if flow is None:
            raise missing_object("flow_id")
        check_state_change(flow, state)
        if state is FlowState.ACTIVE:
            others = [
                mode
                for f in self.state.flows.values()
                if f.group_id == flow.group_id and f.id != flow_id and f.state is FlowState.ACTIVE
                for mode in f.default_for
            ]
            check_default_paths(flow, frozenset[IntakeMode](others))
        saved = replace(flow, state=state)
        self.state.flows[flow_id] = saved
        return saved
