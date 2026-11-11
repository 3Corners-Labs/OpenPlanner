"""SQL for the service flows of a group and their revisions (ADR 0121 step 2b).

A mixin of ``SqlTransaction``. A flow belongs to a group, so every read filters by ``Scope`` on
the group's organisation and slug (ADR 0014). A revision that is published never changes: a draft
is saved over its own unpublished revision, and publishing is a later step (ADR 0121 step 2d).
What SQL cannot express is enforced in ``save_draft``: the pool and every skill belong to the
group's organisation, and a skill is added to a revision only while it is not archived.
"""

import json
from collections import defaultdict
from collections.abc import Collection, Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    CapacityPoolRow,
    FlowRevisionRow,
    FlowRevisionSkillRow,
    GroupRow,
    OrganisationRow,
    ServiceFlowRow,
    SkillRow,
    SubjectFlowRow,
    SubjectFlowSkillRow,
)
from app.adapters.db.scope import scope_filter
from app.domain.authorization import Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ServiceFlow,
)

_Key = tuple[int, int]


def _modes(text: str) -> frozenset[IntakeMode]:
    """Parse the comma-separated ``intake_modes`` column."""
    return frozenset(IntakeMode(mode) for mode in text.split(",") if mode)


def _revision(
    row: FlowRevisionRow,
    skills: Iterable[int],
    subjects: dict[str, set[int]],
) -> FlowRevision:
    """Map a revision row with its skill sets."""
    return FlowRevision(
        flow_id=row.flow_id,
        revision=row.revision,
        labels=json.loads(row.labels),
        intake_modes=_modes(row.intake_modes),
        interaction_kind=InteractionKind(row.interaction_kind) if row.interaction_kind else None,
        duration_minutes=row.duration_minutes,
        capacity_pool_id=row.capacity_pool_id,
        required_skill_ids=frozenset(skills),
        subject_skill_ids={code: frozenset(ids) for code, ids in subjects.items()},
        dispatch=DispatchKind(row.dispatch) if row.dispatch else None,
        handling_action=HandlingAction(row.handling_action) if row.handling_action else None,
        completion=CompletionPolicy(row.completion) if row.completion else None,
        location_id=row.location_id,
        collects_email=row.collects_email,
        legacy=row.legacy,
        published_at=row.published_at,
        published_by=row.published_by,
    )


def _defaults(row: ServiceFlowRow) -> frozenset[IntakeMode]:
    """Return the entry paths the flow is the default of."""
    modes = {
        IntakeMode.APPOINTMENT: row.default_appointment,
        IntakeMode.ON_DEMAND: row.default_on_demand,
    }
    return frozenset(mode for mode, default in modes.items() if default)


def _refused(code: ErrorCode, field: str) -> DomainError:
    """Return a domain error for ``field``."""
    return DomainError(code, field)


class ServiceFlowQueries:
    """The service flows' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def _flows(self, rows: Collection[ServiceFlowRow]) -> list[ServiceFlow]:
        """Map flow rows with their current revisions and skill sets."""
        if not rows:
            return []
        keys = {(row.id, row.current_revision) for row in rows}
        ids = {flow_id for flow_id, _ in keys}
        revisions = {
            (r.flow_id, r.revision): r
            for r in await self._session.scalars(
                select(FlowRevisionRow).where(FlowRevisionRow.flow_id.in_(ids))
            )
            if (r.flow_id, r.revision) in keys
        }
        skills: dict[_Key, set[int]] = defaultdict(set)
        for flow_id, revision, skill_id in (
            await self._session.execute(
                select(
                    FlowRevisionSkillRow.flow_id,
                    FlowRevisionSkillRow.revision,
                    FlowRevisionSkillRow.skill_id,
                ).where(FlowRevisionSkillRow.flow_id.in_(ids))
            )
        ).all():
            skills[(flow_id, revision)].add(skill_id)
        subjects: dict[_Key, dict[str, set[int]]] = defaultdict(dict)
        for flow_id, revision, code in (
            await self._session.execute(
                select(SubjectFlowRow.flow_id, SubjectFlowRow.revision, SubjectFlowRow.subject_code)
                .where(SubjectFlowRow.flow_id.in_(ids))
                .order_by(SubjectFlowRow.subject_code)
            )
        ).all():
            subjects[(flow_id, revision)][code] = set()
        for flow_id, revision, code, skill_id in (
            await self._session.execute(
                select(
                    SubjectFlowSkillRow.flow_id,
                    SubjectFlowSkillRow.revision,
                    SubjectFlowSkillRow.subject_code,
                    SubjectFlowSkillRow.skill_id,
                ).where(SubjectFlowSkillRow.flow_id.in_(ids))
            )
        ).all():
            subjects[(flow_id, revision)][code].add(skill_id)
        return [
            ServiceFlow(
                row.id,
                row.group_id,
                row.code,
                FlowState(row.state),
                _defaults(row),
                _revision(
                    revisions[(row.id, row.current_revision)],
                    skills[(row.id, row.current_revision)],
                    subjects[(row.id, row.current_revision)],
                ),
            )
            for row in rows
        ]

    async def list_service_flows(self, scope: Scope, group_id: int) -> list[ServiceFlow]:
        """Return the flows of the group inside ``scope`` with their current revisions, by id."""
        rows = await self._session.scalars(
            select(ServiceFlowRow)
            .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(
                ServiceFlowRow.group_id == group_id,
                scope_filter(scope, OrganisationRow.slug, GroupRow.slug),
            )
            .order_by(ServiceFlowRow.id)
        )
        return await self._flows(list(rows))

    async def get_service_flow(self, scope: Scope, flow_id: int) -> ServiceFlow | None:
        """Return one flow, or ``None`` when it does not exist or lies outside ``scope``."""
        row = await self._session.scalar(
            select(ServiceFlowRow)
            .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(
                ServiceFlowRow.id == flow_id,
                scope_filter(scope, OrganisationRow.slug, GroupRow.slug),
            )
        )
        return (await self._flows([row]))[0] if row is not None else None

    async def flow_revision(self, flow_id: int, revision: int) -> FlowRevision | None:
        """Return one revision of a flow, published or not, or ``None``."""
        saved = await self._session.get(FlowRevisionRow, (flow_id, revision))
        if saved is None:
            return None
        skills = await self._session.scalars(
            select(FlowRevisionSkillRow.skill_id).where(
                FlowRevisionSkillRow.flow_id == flow_id, FlowRevisionSkillRow.revision == revision
            )
        )
        subjects: dict[str, set[int]] = {}
        for code in await self._session.scalars(
            select(SubjectFlowRow.subject_code)
            .where(SubjectFlowRow.flow_id == flow_id, SubjectFlowRow.revision == revision)
            .order_by(SubjectFlowRow.subject_code)
        ):
            subjects[code] = set()
        for code, skill_id in (
            await self._session.execute(
                select(SubjectFlowSkillRow.subject_code, SubjectFlowSkillRow.skill_id).where(
                    SubjectFlowSkillRow.flow_id == flow_id, SubjectFlowSkillRow.revision == revision
                )
            )
        ).all():
            subjects[code].add(skill_id)
        return _revision(saved, skills, subjects)

    async def _check_flow_references(
        self, organisation_id: int, revision: FlowRevision, already: set[int]
    ) -> None:
        """Refuse a pool or skill of another organisation, or a skill added while archived."""
        if revision.capacity_pool_id is not None:
            pool = await self._session.get(CapacityPoolRow, revision.capacity_pool_id)
            if pool is None:
                raise _refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "capacity_pool_id")
            if pool.organisation_id != organisation_id:
                raise _refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "capacity_pool_id")
        wanted = set(revision.required_skill_ids).union(*revision.subject_skill_ids.values())
        if not wanted:
            return
        rows = (
            await self._session.execute(
                select(SkillRow.id, SkillRow.organisation_id, SkillRow.archived_at).where(
                    SkillRow.id.in_(wanted)
                )
            )
        ).all()
        if len(rows) != len(wanted):
            raise _refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "required_skill_ids")
        if any(organisation != organisation_id for _, organisation, _ in rows):
            raise _refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "required_skill_ids")
        if any(archived is not None and skill_id not in already for skill_id, _, archived in rows):
            raise _refused(ErrorCode.INVALID_CONFIGURATION, "required_skill_ids")

    async def _write_revision(self, flow_id: int, revision: FlowRevision) -> None:
        """Replace the revision row and its skill and subject rows with ``revision``."""
        key = (FlowRevisionRow.flow_id == flow_id) & (FlowRevisionRow.revision == revision.revision)
        await self._session.execute(delete(FlowRevisionRow).where(key))
        self._session.add(
            FlowRevisionRow(
                flow_id=flow_id,
                revision=revision.revision,
                labels=json.dumps(dict(revision.labels), sort_keys=True),
                intake_modes=",".join(sorted(mode.value for mode in revision.intake_modes)),
                interaction_kind=revision.interaction_kind.value
                if revision.interaction_kind
                else None,
                duration_minutes=revision.duration_minutes,
                capacity_pool_id=revision.capacity_pool_id,
                dispatch=revision.dispatch.value if revision.dispatch else None,
                handling_action=revision.handling_action.value
                if revision.handling_action
                else None,
                completion=revision.completion.value if revision.completion else None,
                location_id=revision.location_id,
                collects_email=revision.collects_email,
                legacy=revision.legacy,
            )
        )
        await self._session.flush()
        self._session.add_all(
            FlowRevisionSkillRow(flow_id=flow_id, revision=revision.revision, skill_id=skill_id)
            for skill_id in sorted(revision.required_skill_ids)
        )
        for code, skill_ids in sorted(revision.subject_skill_ids.items()):
            self._session.add(
                SubjectFlowRow(flow_id=flow_id, revision=revision.revision, subject_code=code)
            )
            await self._session.flush()
            self._session.add_all(
                SubjectFlowSkillRow(
                    flow_id=flow_id,
                    revision=revision.revision,
                    subject_code=code,
                    skill_id=skill_id,
                )
                for skill_id in sorted(skill_ids)
            )
        await self._session.flush()

    async def _held_skills(self, flow_id: int, revision: int) -> set[int]:
        """Return the skills the stored revision requires, for the flow or a subject."""
        own = await self._session.scalars(
            select(FlowRevisionSkillRow.skill_id).where(
                FlowRevisionSkillRow.flow_id == flow_id, FlowRevisionSkillRow.revision == revision
            )
        )
        extra = await self._session.scalars(
            select(SubjectFlowSkillRow.skill_id).where(
                SubjectFlowSkillRow.flow_id == flow_id, SubjectFlowSkillRow.revision == revision
            )
        )
        return {*own, *extra}

    async def save_draft(self, flow: ServiceFlow) -> ServiceFlow:
        """Create a draft flow (no id, revision 1) or overwrite its unpublished current revision.

        A published revision is immutable: saving over one is refused (a new revision of a
        published flow arrives with publishing, ADR 0121 step 2d). The state is never changed
        here; a new flow is a draft. The pool and every skill must belong to the group's
        organisation, and a skill is newly required only while it is not archived (an archived
        one the revision already requires stays, A28).

        Raises:
            DomainError: ``group_not_found``, ``configuration_object_missing`` (unknown flow,
                pool or skill), ``configuration_out_of_scope`` (a pool, skill or group of
                another organisation or group), ``slug_taken`` (field ``flow_code``: the group
                has the code already) and ``invalid_configuration`` with field ``state``,
                ``revision``, ``published_at`` or ``revision_published`` for a draft that is
                none, is not revision 1 or the stored current one, or overwrites a published one.
        """
        group = await self._session.get(GroupRow, flow.group_id)
        if group is None:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        revision = flow.current
        if flow.state is not FlowState.DRAFT:
            raise _refused(ErrorCode.INVALID_CONFIGURATION, "state")
        if revision.published_at is not None:
            raise _refused(ErrorCode.INVALID_CONFIGURATION, "published_at")
        row: ServiceFlowRow | None = None
        already: set[int] = set()
        if flow.id is None:
            if revision.revision != 1:
                raise _refused(ErrorCode.INVALID_CONFIGURATION, "revision")
        else:
            row = await self._session.get(ServiceFlowRow, flow.id)
            if row is None:
                raise _refused(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id")
            if row.group_id != flow.group_id:
                raise _refused(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "group_id")
            if row.state != FlowState.DRAFT.value:
                raise _refused(ErrorCode.INVALID_CONFIGURATION, "state")
            if revision.revision != row.current_revision:
                raise _refused(ErrorCode.INVALID_CONFIGURATION, "revision")
            stored = await self._session.get(FlowRevisionRow, (row.id, row.current_revision))
            if stored is not None and stored.published_at is not None:
                raise _refused(ErrorCode.INVALID_CONFIGURATION, "revision_published")
            already = await self._held_skills(row.id, row.current_revision)
        taken = await self._session.scalar(
            select(ServiceFlowRow.id).where(
                ServiceFlowRow.group_id == flow.group_id,
                ServiceFlowRow.code == flow.code,
                ServiceFlowRow.id != (flow.id or 0),
            )
        )
        if taken is not None:
            raise _refused(ErrorCode.SLUG_TAKEN, "flow_code")
        await self._check_flow_references(group.organisation_id, revision, already)
        if row is None:
            row = ServiceFlowRow(group_id=flow.group_id, state=FlowState.DRAFT.value)
            self._session.add(row)
        row.code = flow.code
        row.default_appointment = IntakeMode.APPOINTMENT in flow.default_for
        row.default_on_demand = IntakeMode.ON_DEMAND in flow.default_for
        row.current_revision = revision.revision
        await self._session.flush()
        await self._write_revision(row.id, revision)
        (stored_flow,) = await self._flows([row])
        return stored_flow
