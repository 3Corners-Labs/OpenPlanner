"""SQL for the service flows of a group and their revisions (ADR 0121 step 2b).

A mixin of ``SqlTransaction``. A flow belongs to a group, so every method that takes an id
filters by ``Scope`` on the group's organisation and slug in SQL (ADR 0014); an id outside the
scope behaves like a missing one. A flow has at most two revisions in play: ``current_revision``,
the published one, and ``draft_revision``, the one being edited. A published revision never
changes. The rules that SQL cannot express (the pool and skills belong to the group's
organisation, the subjects are the group's, a state change, a free default entry path) are the
pure functions of ``app.domain.service_flows``, called here with the rows already loaded. Whether a
revision is *ready* to be published or activated is the readiness checklist, which the use case of
ADR 0121 step 2d runs first; nothing here reads it.

Writes run in ``locked()`` transactions (``BEGIN IMMEDIATE``), so the pre-checks that select
before they insert (the code of a flow, the free default path) cannot interleave with another
writer; the unique constraint and the partial unique indexes stay as the backstop.
"""

import json
from collections import defaultdict
from collections.abc import Collection, Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.adapters.db.capacity_pools import pools_from_rows
from app.adapters.db.models import (
    CapacityPoolRow,
    FlowRevisionRow,
    FlowRevisionSkillRow,
    GroupRow,
    OrganisationRow,
    ServiceFlowRow,
    SubjectFlowRow,
    SubjectFlowSkillRow,
)
from app.adapters.db.routing_json import routing_from_json
from app.adapters.db.scope import scope_filter
from app.adapters.db.skills import load_skills
from app.domain.authorization import Scope
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ServiceFlow,
    check_default_paths,
    check_draft_save,
    check_flow_references,
    check_publishable,
    check_state_change,
)

_Key = tuple[int, int]


def _optional[E: StrEnum](kind: type[E], value: str | None) -> E | None:
    """Return the enum member stored as ``value``, ``None`` for a NULL column."""
    return kind(value) if value else None


def _text(member: StrEnum | None) -> str | None:
    """Return the text stored for an optional enum member."""
    return member.value if member else None


def _from_row(
    row: FlowRevisionRow, skills: Iterable[int], subjects: dict[str, set[int]]
) -> FlowRevision:
    """Map a revision row with its skill sets."""
    return FlowRevision(
        flow_id=row.flow_id,
        revision=row.revision,
        labels=json.loads(row.labels),
        intake_modes=frozenset(IntakeMode(mode) for mode in row.intake_modes.split(",") if mode),
        interaction_kind=_optional(InteractionKind, row.interaction_kind),
        duration_minutes=row.duration_minutes,
        capacity_pool_id=row.capacity_pool_id,
        required_skill_ids=frozenset(skills),
        subject_skill_ids={code: frozenset(ids) for code, ids in subjects.items()},
        dispatch=_optional(DispatchKind, row.dispatch),
        handling_action=_optional(HandlingAction, row.handling_action),
        completion=_optional(CompletionPolicy, row.completion),
        location_id=row.location_id,
        collects_email=row.collects_email,
        legacy=row.legacy,
        published_at=row.published_at,
        published_by=row.published_by,
    )


def _to_row(row: FlowRevisionRow, revision: FlowRevision) -> None:
    """Copy a revision's own columns onto ``row``; publishing is not part of a save."""
    row.labels = json.dumps(dict(revision.labels), sort_keys=True)
    row.intake_modes = ",".join(sorted(mode.value for mode in revision.intake_modes))
    row.interaction_kind = _text(revision.interaction_kind)
    row.duration_minutes = revision.duration_minutes
    row.capacity_pool_id = revision.capacity_pool_id
    row.dispatch = _text(revision.dispatch)
    row.handling_action = _text(revision.handling_action)
    row.completion = _text(revision.completion)
    row.location_id = revision.location_id
    row.collects_email = revision.collects_email
    row.legacy = revision.legacy


def _defaults(row: ServiceFlowRow) -> frozenset[IntakeMode]:
    """Return the entry paths the flow is the default of."""
    modes = {
        IntakeMode.APPOINTMENT: row.default_appointment,
        IntakeMode.ON_DEMAND: row.default_on_demand,
    }
    return frozenset(mode for mode, default in modes.items() if default)


def _skills_of(revision: FlowRevision | None) -> frozenset[int]:
    """Return every skill a revision requires, for the flow or for a subject."""
    if revision is None:
        return frozenset()
    return revision.required_skill_ids.union(*revision.subject_skill_ids.values())


class ServiceFlowQueries:
    """The service flows' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def _revisions(self, keys: Collection[_Key]) -> dict[_Key, FlowRevision]:
        """Return the revisions named by ``(flow id, revision)`` pairs, filtered in SQL."""
        if not keys:
            return {}
        pairs = sorted(keys)

        def named(flow_id: Any, revision: Any) -> ColumnElement[bool]:
            return tuple_(flow_id, revision).in_(pairs)

        rows = await self._session.scalars(
            select(FlowRevisionRow).where(named(FlowRevisionRow.flow_id, FlowRevisionRow.revision))
        )
        skills: dict[_Key, set[int]] = defaultdict(set)
        for flow_id, revision, skill_id in (
            await self._session.execute(
                select(
                    FlowRevisionSkillRow.flow_id,
                    FlowRevisionSkillRow.revision,
                    FlowRevisionSkillRow.skill_id,
                ).where(named(FlowRevisionSkillRow.flow_id, FlowRevisionSkillRow.revision))
            )
        ).all():
            skills[(flow_id, revision)].add(skill_id)
        subjects: dict[_Key, dict[str, set[int]]] = defaultdict(dict)
        for flow_id, revision, code in (
            await self._session.execute(
                select(SubjectFlowRow.flow_id, SubjectFlowRow.revision, SubjectFlowRow.subject_code)
                .where(named(SubjectFlowRow.flow_id, SubjectFlowRow.revision))
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
                ).where(named(SubjectFlowSkillRow.flow_id, SubjectFlowSkillRow.revision))
            )
        ).all():
            subjects[(flow_id, revision)][code].add(skill_id)
        return {
            (row.flow_id, row.revision): _from_row(
                row, skills[(row.flow_id, row.revision)], subjects[(row.flow_id, row.revision)]
            )
            for row in rows
        }

    async def _flows(self, rows: Collection[ServiceFlowRow]) -> list[ServiceFlow]:
        """Map flow rows with their current and draft revisions."""
        keys = {
            (row.id, number)
            for row in rows
            for number in (row.current_revision, row.draft_revision)
            if number is not None
        }
        found = await self._revisions(keys)
        return [
            ServiceFlow(
                row.id,
                row.group_id,
                row.code,
                FlowState(row.state),
                _defaults(row),
                found[(row.id, row.current_revision)] if row.current_revision else None,
                found[(row.id, row.draft_revision)] if row.draft_revision else None,
            )
            for row in rows
        ]

    async def _flow_row(self, scope: Scope, flow_id: int) -> ServiceFlowRow | None:
        """Return the flow row inside ``scope``, or ``None`` when missing or outside it."""
        return await self._session.scalar(
            select(ServiceFlowRow)
            .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(
                ServiceFlowRow.id == flow_id,
                scope_filter(scope, OrganisationRow.slug, GroupRow.slug),
            )
        )

    async def list_service_flows(self, scope: Scope, group_id: int) -> list[ServiceFlow]:
        """Return the flows of the group inside ``scope`` with current and draft, by id."""
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
        row = await self._flow_row(scope, flow_id)
        return (await self._flows([row]))[0] if row is not None else None

    async def flow_revision(self, flow_id: int, revision: int) -> FlowRevision | None:
        """Return one revision of a flow, published or not, or ``None``.

        Deliberately without a ``Scope``: the resolver of ADR 0121 step 3 reads the revision of a
        flow it has already resolved for a booking, and the booking's own checks decide who sees
        what. A caller that gets a flow id from a person uses ``get_service_flow`` instead.
        """
        return (await self._revisions([(flow_id, revision)])).get((flow_id, revision))

    async def _write_revision(self, flow_id: int, number: int, revision: FlowRevision) -> None:
        """Write the revision row and its skill and subject rows, in place when it exists.

        Only ever called for a revision that is not published.
        """
        row = await self._session.get(FlowRevisionRow, (flow_id, number))
        if row is None:
            row = FlowRevisionRow(flow_id=flow_id, revision=number)
            self._session.add(row)
        else:
            for child in (SubjectFlowSkillRow, SubjectFlowRow, FlowRevisionSkillRow):
                await self._session.execute(
                    delete(child).where(child.flow_id == flow_id, child.revision == number)
                )
        _to_row(row, revision)
        await self._session.flush()
        self._session.add_all(
            FlowRevisionSkillRow(flow_id=flow_id, revision=number, skill_id=skill_id)
            for skill_id in sorted(revision.required_skill_ids)
        )
        self._session.add_all(
            SubjectFlowRow(flow_id=flow_id, revision=number, subject_code=code)
            for code in sorted(revision.subject_skill_ids)
        )
        await self._session.flush()
        self._session.add_all(
            SubjectFlowSkillRow(
                flow_id=flow_id, revision=number, subject_code=code, skill_id=skill_id
            )
            for code, skill_ids in sorted(revision.subject_skill_ids.items())
            for skill_id in sorted(skill_ids)
        )
        await self._session.flush()

    async def save_draft(self, scope: Scope, flow: ServiceFlow) -> ServiceFlow:
        """Create a draft flow (no id) or write the draft revision of an existing flow.

        The content is ``flow.draft``. A new flow is stored in state ``draft`` with draft
        revision 1 and no current revision. For an existing flow the draft is written over its
        own unpublished revision, or, when there is none, becomes revision current + 1; the
        published revision is never changed, and once a flow has one its code and defaults stay.

        Raises:
            DomainError: ``group_not_found`` for a group that does not exist or lies outside
                ``scope``, ``configuration_object_missing`` for a flow that does not, and what
                ``check_draft_save`` and ``check_flow_references`` refuse.
        """
        group = await self._session.scalar(
            select(GroupRow)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(
                GroupRow.id == flow.group_id,
                scope_filter(scope, OrganisationRow.slug, GroupRow.slug),
            )
        )
        if group is None:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        row = None if flow.id is None else await self._flow_row(scope, flow.id)
        stored = (await self._flows([row]))[0] if row is not None else None
        taken = await self._session.scalar(
            select(ServiceFlowRow.id).where(
                ServiceFlowRow.group_id == flow.group_id,
                ServiceFlowRow.code == flow.code,
                ServiceFlowRow.id != (flow.id or 0),
            )
        )
        revision = check_draft_save(flow, stored, taken is not None)
        based_on = (stored.draft or stored.current) if stored else None
        pool_row = (
            await self._session.get(CapacityPoolRow, revision.capacity_pool_id)
            if revision.capacity_pool_id is not None
            else None
        )
        check_flow_references(
            group.organisation_id,
            revision,
            (await pools_from_rows(self._session, [pool_row]))[0] if pool_row else None,
            await load_skills(self._session, _skills_of(revision)),
            _skills_of(based_on),
            [subject.code for subject in routing_from_json(group.routing).subjects],
            already_pool_id=based_on.capacity_pool_id if based_on else None,
        )
        if row is None:
            row = ServiceFlowRow(group_id=flow.group_id, state=FlowState.DRAFT.value)
            self._session.add(row)
        if stored is None or stored.current is None:
            row.code = flow.code
            row.default_appointment = IntakeMode.APPOINTMENT in flow.default_for
            row.default_on_demand = IntakeMode.ON_DEMAND in flow.default_for
        if stored is not None and stored.draft is not None:
            number = stored.draft.revision
        else:
            number = (stored.current.revision if stored and stored.current else 0) + 1
        row.draft_revision = number
        await self._session.flush()
        await self._write_revision(row.id, number, revision)
        return (await self._flows([row]))[0]

    async def publish_draft(
        self, scope: Scope, flow_id: int, *, by: int, at: datetime
    ) -> ServiceFlow:
        """Publish the flow's draft: it becomes the current revision and is immutable from now on.

        Raises:
            DomainError: ``configuration_object_missing`` for a flow outside ``scope`` or a flow
                without a draft (field ``draft``), ``invalid_configuration`` (field
                ``default_for``) for a draft that drops a default entry path.
        """
        row = await self._flow_row(scope, flow_id)
        if row is None:
            raise missing_object("flow_id")
        (flow,) = await self._flows([row])
        check_publishable(flow)
        revision = await self._session.get(FlowRevisionRow, (flow_id, row.draft_revision or 0))
        if revision is None:
            raise missing_object("draft")
        revision.published_at = at
        revision.published_by = by
        row.current_revision = row.draft_revision
        row.draft_revision = None
        await self._session.flush()
        return (await self._flows([row]))[0]

    async def discard_draft(self, scope: Scope, flow_id: int) -> None:
        """Delete the flow's unpublished draft; a flow never published goes with it.

        Raises:
            DomainError: ``configuration_object_missing`` for a flow outside ``scope`` or a flow
                without a draft (field ``draft``).
        """
        row = await self._flow_row(scope, flow_id)
        if row is None:
            raise missing_object("flow_id")
        if row.draft_revision is None:
            raise missing_object("draft")
        if row.current_revision is None:
            await self._session.delete(row)  # the database removes the revision with it
        else:
            await self._session.execute(
                delete(FlowRevisionRow).where(
                    FlowRevisionRow.flow_id == flow_id,
                    FlowRevisionRow.revision == row.draft_revision,
                )
            )
            row.draft_revision = None
        await self._session.flush()

    async def set_flow_state(self, scope: Scope, flow_id: int, state: FlowState) -> ServiceFlow:
        """Change the state of a flow: draft to active, active to archived, archived to active.

        Activating needs a published revision and a default entry path that no other active flow
        of the group has. The readiness checklist is the caller's check.

        Raises:
            DomainError: ``configuration_object_missing`` for a flow outside ``scope``, and what
                ``check_state_change`` and ``check_default_paths`` refuse.
        """
        row = await self._flow_row(scope, flow_id)
        if row is None:
            raise missing_object("flow_id")
        (flow,) = await self._flows([row])
        check_state_change(flow, state)
        if state is FlowState.ACTIVE:
            others = await self._session.scalars(
                select(ServiceFlowRow).where(
                    ServiceFlowRow.group_id == row.group_id,
                    ServiceFlowRow.state == FlowState.ACTIVE.value,
                    ServiceFlowRow.id != flow_id,
                )
            )
            check_default_paths(flow, frozenset().union(*(_defaults(other) for other in others)))
        row.state = state.value
        await self._session.flush()
        return (await self._flows([row]))[0]
