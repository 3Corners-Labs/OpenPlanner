"""Skills, capacity pools and flows in a configuration document (ADR 0121 step 2b-ii).

``read_catalogue`` and ``read_flows`` turn the nine tables of migration 0109 into the ``Entity``
objects the version-1 format describes (``app.domain.configuration_v1_skills`` and
``app.domain.configuration_v1_flows``): every reference is a natural key (a skill by code, a counted
pool by name, a legacy pool by its group's slug, a revision by number) and no id and no staff
identity leaves the database. ``WRITERS`` apply one planned ``Change`` inside the caller's
transaction, as the other ``config_*`` modules do: no audit event, no settings version, no SQL
outside the ORM.

Which person holds which skill (``staff_skills``) is state, not configuration: it is neither read
nor written here, and no write here removes a grant, because a skill that a person holds counts as
used (``flow_usage``) and is archived instead of deleted. A published revision is never written
again: the planner refuses a change to one, and the writers refuse it a second time.
"""

import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import delete, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.config_common import (
    Writer,
    find_organisation_id,
    invalid,
    moment,
    row_or_refuse,
)
from app.adapters.db.models import (
    BookingRow,
    CapacityPoolRow,
    CapacityPoolSkillRow,
    FlowRevisionRow,
    FlowRevisionSkillRow,
    GroupRow,
    OrganisationRow,
    ServiceFlowRow,
    SkillRow,
    StaffSkillRow,
    SubjectFlowRow,
    SubjectFlowSkillRow,
)
from app.domain.capacity import PoolMode
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Address, Entity
from app.domain.configuration_v1_names import (
    CAPACITY_POOL_KIND,
    FLOW_REVISION_KIND,
    LEGACY_POOL_KIND,
    SERVICE_FLOW_KIND,
    SKILL_KIND,
)
from app.domain.configuration_values import Json, Value
from app.domain.errors import DomainError, ErrorCode
from app.domain.service_flows import IntakeMode

type Sections = dict[str, tuple[Entity, ...]]
"""The collections a reader hands to the assembler: section name to its objects."""


# --- reading ------------------------------------------------------------------------------


async def read_catalogue(session: AsyncSession) -> dict[str, Sections]:
    """Return every organisation's ``skills`` and counted ``capacity_pools``, by slug.

    Raises:
        DomainError: ``configuration_object_exists`` (field ``capacity_pool``) when two counted
            pools of one organisation share a name: the name is their natural key, and an export
            would otherwise silently drop one of them. The pool screen has to keep names unique.
    """
    skills: dict[str, list[Entity]] = defaultdict(list)
    for row, organisation in (
        await session.execute(
            select(SkillRow, OrganisationRow.slug)
            .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
            .order_by(OrganisationRow.slug, SkillRow.code)
        )
    ).all():
        skills[organisation].append(
            Entity(
                SKILL_KIND,
                (row.code,),
                fields={"labels": json.loads(row.labels), "archived": row.archived_at is not None},
            )
        )
    codes = await _skill_codes(session)
    guaranteed: dict[int, list[str]] = defaultdict(list)
    for pool_id, skill_id in (
        await session.execute(select(CapacityPoolSkillRow.pool_id, CapacityPoolSkillRow.skill_id))
    ).all():
        guaranteed[pool_id].append(codes[skill_id])
    pools: dict[str, list[Entity]] = defaultdict(list)
    for pool_row, organisation in (
        await session.execute(
            select(CapacityPoolRow, OrganisationRow.slug)
            .join(OrganisationRow, OrganisationRow.id == CapacityPoolRow.organisation_id)
            .where(CapacityPoolRow.mode == PoolMode.COUNTED.value)
            .order_by(OrganisationRow.slug, CapacityPoolRow.name, CapacityPoolRow.id)
        )
    ).all():
        if any(found.key == (pool_row.name,) for found in pools[organisation]):
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_EXISTS, "capacity_pool")
        pools[organisation].append(
            Entity(
                CAPACITY_POOL_KIND,
                (pool_row.name,),
                fields={
                    "guaranteed_skills": cast("Json", sorted(guaranteed[pool_row.id])),
                    "archived": pool_row.archived_at is not None,
                },
            )
        )
    return {
        slug: {
            name: tuple(found[slug])
            for name, found in (("skills", skills), ("capacity_pools", pools))
            if found.get(slug)
        }
        for slug in {*skills, *pools}
    }


async def _skill_codes(session: AsyncSession) -> dict[int, str]:
    """Return the code of every skill by id."""
    return {
        skill_id: code
        for skill_id, code in (await session.execute(select(SkillRow.id, SkillRow.code))).all()
    }


async def read_flows(session: AsyncSession) -> dict[Address, Sections]:
    """Return every group's ``legacy_pool`` and ``flows`` with their revisions, by address.

    The address is the organisation's and the group's slug. A flow carries every revision it has,
    published or not, with ``published_at`` as the only trace of who and when; ``published_by``
    is a staff id and stays in the database.
    """
    codes = await _skill_codes(session)
    pools = (
        await session.execute(
            select(CapacityPoolRow, OrganisationRow.slug, GroupRow.slug)
            .join(OrganisationRow, OrganisationRow.id == CapacityPoolRow.organisation_id)
            .outerjoin(GroupRow, GroupRow.id == CapacityPoolRow.group_id)
            .order_by(CapacityPoolRow.id)
        )
    ).all()
    guaranteed: dict[int, list[str]] = defaultdict(list)
    for pool_id, skill_id in (
        await session.execute(select(CapacityPoolSkillRow.pool_id, CapacityPoolSkillRow.skill_id))
    ).all():
        guaranteed[pool_id].append(codes[skill_id])
    reference: dict[int, tuple[str, str]] = {}
    sections: dict[Address, Sections] = defaultdict(dict)
    for pool, organisation, group in pools:
        if group is None:
            reference[pool.id] = ("capacity_pool", pool.name)
            continue
        reference[pool.id] = ("legacy_pool_of", group)
        sections[(organisation, group)]["legacy_pool"] = (
            Entity(
                LEGACY_POOL_KIND,
                (),
                fields={
                    "name": pool.name,
                    "guaranteed_skills": cast("Json", sorted(guaranteed[pool.id])),
                    "archived": pool.archived_at is not None,
                },
            ),
        )
    revisions = await _revisions(session, codes, reference)
    flows: dict[Address, list[Entity]] = defaultdict(list)
    for flow, organisation, group in (
        await session.execute(
            select(ServiceFlowRow, OrganisationRow.slug, GroupRow.slug)
            .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .order_by(ServiceFlowRow.code)
        )
    ).all():
        modes = [
            mode.value
            for mode, default in (
                (IntakeMode.APPOINTMENT, flow.default_appointment),
                (IntakeMode.ON_DEMAND, flow.default_on_demand),
            )
            if default
        ]
        flows[(organisation, group)].append(
            Entity(
                SERVICE_FLOW_KIND,
                (flow.code,),
                fields={
                    "state": flow.state,
                    "default_for": cast("Json", sorted(modes)),
                    "current_revision": flow.current_revision,
                    "draft_revision": flow.draft_revision,
                },
                children={"revisions": tuple(revisions.get(flow.id, ()))},
            )
        )
    for address, found in flows.items():
        sections[address]["flows"] = tuple(found)
    return dict(sections)


async def _revisions(
    session: AsyncSession, codes: dict[int, str], reference: dict[int, tuple[str, str]]
) -> dict[int, list[Entity]]:
    """Return the revision objects of every flow, by flow id, in order of number."""
    required: dict[tuple[int, int], list[str]] = defaultdict(list)
    for flow_id, number, skill_id in (
        await session.execute(
            select(
                FlowRevisionSkillRow.flow_id,
                FlowRevisionSkillRow.revision,
                FlowRevisionSkillRow.skill_id,
            )
        )
    ).all():
        required[(flow_id, number)].append(codes[skill_id])
    subjects: dict[tuple[int, int], dict[str, list[str]]] = defaultdict(dict)
    for flow_id, number, subject in (
        await session.execute(
            select(SubjectFlowRow.flow_id, SubjectFlowRow.revision, SubjectFlowRow.subject_code)
        )
    ).all():
        subjects[(flow_id, number)][subject] = []
    for flow_id, number, subject, skill_id in (
        await session.execute(
            select(
                SubjectFlowSkillRow.flow_id,
                SubjectFlowSkillRow.revision,
                SubjectFlowSkillRow.subject_code,
                SubjectFlowSkillRow.skill_id,
            )
        )
    ).all():
        subjects[(flow_id, number)][subject].append(codes[skill_id])
    found: dict[int, list[Entity]] = defaultdict(list)
    for row in await session.scalars(
        select(FlowRevisionRow).order_by(FlowRevisionRow.flow_id, FlowRevisionRow.revision)
    ):
        key = (row.flow_id, row.revision)
        pool_field, pool_value = (
            reference.get(row.capacity_pool_id, ("capacity_pool", None))
            if row.capacity_pool_id is not None
            else ("capacity_pool", None)
        )
        fields: dict[str, Value] = {
            "labels": json.loads(row.labels),
            "intake_modes": cast(
                "Json", sorted(mode for mode in row.intake_modes.split(",") if mode)
            ),
            "interaction_kind": row.interaction_kind,
            "duration_minutes": row.duration_minutes,
            "capacity_pool": None,
            "legacy_pool_of": None,
            "required_skills": cast("Json", sorted(required[key])),
            "subjects": cast("Json", {c: sorted(i) for c, i in sorted(subjects[key].items())}),
            "dispatch": row.dispatch,
            "handling_action": row.handling_action,
            "completion": row.completion,
            "collects_email": row.collects_email,
            "legacy": row.legacy,
            "published_at": moment(row.published_at),
        }
        fields[pool_field] = pool_value
        found[row.flow_id].append(Entity(FLOW_REVISION_KIND, (str(row.revision),), fields=fields))
    return found


async def flow_usage(session: AsyncSession) -> dict[str, frozenset[tuple[str, ...]]]:
    """Return what is in use by something outside the document, by kind and address.

    A skill is in use when a person holds it, a pool guarantees it or any revision (old ones
    too) or subject requires it; a counted pool when any revision draws on it; a flow when a
    booking was made under it. Such an object is archived, never deleted, by a document that
    leaves it out, because deleting it would silently remove history or grants.
    """
    skill_users = union(
        select(StaffSkillRow.skill_id),
        select(CapacityPoolSkillRow.skill_id),
        select(FlowRevisionSkillRow.skill_id),
        select(SubjectFlowSkillRow.skill_id),
    )
    skills = await session.execute(
        select(OrganisationRow.slug, SkillRow.code)
        .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
        .where(SkillRow.id.in_(skill_users))
    )
    pools = await session.execute(
        select(OrganisationRow.slug, CapacityPoolRow.name)
        .join(OrganisationRow, OrganisationRow.id == CapacityPoolRow.organisation_id)
        .where(
            CapacityPoolRow.mode == PoolMode.COUNTED.value,
            CapacityPoolRow.id.in_(
                select(FlowRevisionRow.capacity_pool_id).where(
                    FlowRevisionRow.capacity_pool_id.is_not(None)
                )
            ),
        )
    )
    flows = await session.execute(
        select(OrganisationRow.slug, GroupRow.slug, ServiceFlowRow.code)
        .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
        .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
        .where(
            ServiceFlowRow.id.in_(select(BookingRow.flow_id).where(BookingRow.flow_id.is_not(None)))
        )
    )
    return {
        SKILL_KIND: frozenset((org, code) for org, code in skills.all()),
        CAPACITY_POOL_KIND: frozenset((org, name) for org, name in pools.all()),
        SERVICE_FLOW_KIND: frozenset((org, group, code) for org, group, code in flows.all()),
    }


# --- writing ------------------------------------------------------------------------------


def _names(change: Change) -> set[str]:
    """Return the fields a change writes: all of them on create, else the named ones."""
    return set(change.values) if change.action is ChangeAction.CREATE else set(change.fields)


def _archived_at(was: datetime | None, archived: Value) -> datetime | None:
    """Return the archive time a flag gives: kept when archived already, else now, else none."""
    if not archived:
        return None
    return was or datetime.now(UTC)


def _list(values: Mapping[str, Value], name: str) -> list[str]:
    """Return the texts of a list field of a change."""
    value = values.get(name)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


async def _skill_ids(session: AsyncSession, organisation_id: int) -> dict[str, int]:
    """Return the id of every skill of an organisation by code."""
    return {
        code: skill_id
        for skill_id, code in (
            await session.execute(
                select(SkillRow.id, SkillRow.code).where(
                    SkillRow.organisation_id == organisation_id
                )
            )
        ).all()
    }


async def write_skill(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a skill: create it, change or archive it, or delete it."""
    organisation_id = await find_organisation_id(session, change.address[0])
    code = change.key[0]
    if change.action is ChangeAction.DELETE:
        await session.execute(
            delete(SkillRow).where(
                SkillRow.organisation_id == organisation_id, SkillRow.code == code
            )
        )
        return
    names, values = _names(change), change.values
    if change.action is ChangeAction.CREATE:
        row = SkillRow(organisation_id=organisation_id, code=code, labels="{}")
        session.add(row)
    else:
        row = await row_or_refuse(
            session,
            select(SkillRow).where(
                SkillRow.organisation_id == organisation_id, SkillRow.code == code
            ),
            ErrorCode.CONFIGURATION_OBJECT_MISSING,
            "skill_id",
        )
    if "labels" in names:
        row.labels = json.dumps(values["labels"], sort_keys=True)
    if "archived" in names:
        row.archived_at = _archived_at(row.archived_at, values.get("archived"))
    await session.flush()


async def _write_guaranteed(
    session: AsyncSession, pool: CapacityPoolRow, values: Mapping[str, Value]
) -> None:
    """Replace the skills a pool guarantees by those the change names."""
    ids = await _skill_ids(session, pool.organisation_id)
    wanted = set(_list(values, "guaranteed_skills"))
    if not wanted <= set(ids):
        raise invalid("guaranteed_skill_ids")
    await session.execute(
        delete(CapacityPoolSkillRow).where(CapacityPoolSkillRow.pool_id == pool.id)
    )
    session.add_all(
        CapacityPoolSkillRow(pool_id=pool.id, skill_id=ids[code]) for code in sorted(wanted)
    )
    await session.flush()


async def _write_pool(
    session: AsyncSession, change: Change, row: CapacityPoolRow | None, mode: PoolMode, name: str
) -> None:
    """Write a pool (counted or a group's legacy pool) as its change says."""
    names, values = _names(change), change.values
    if row is None:
        group_id = None
        if mode is PoolMode.LEGACY_GROUP:
            group_id = (await _group_row(session, change.address[1])).id
        row = CapacityPoolRow(
            organisation_id=await find_organisation_id(session, change.address[0]),
            name=name,
            mode=mode.value,
            group_id=group_id,
        )
        session.add(row)
        await session.flush()
    if "name" in names and mode is PoolMode.LEGACY_GROUP:
        row.name = str(values["name"])
    if "archived" in names:
        row.archived_at = _archived_at(row.archived_at, values.get("archived"))
    await session.flush()
    if "guaranteed_skills" in names:
        await _write_guaranteed(session, row, values)


async def write_capacity_pool(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a counted pool, addressed by its name within the organisation."""
    organisation_id = await find_organisation_id(session, change.address[0])
    name = change.key[0]
    row = await session.scalar(
        select(CapacityPoolRow).where(
            CapacityPoolRow.organisation_id == organisation_id,
            CapacityPoolRow.name == name,
            CapacityPoolRow.mode == PoolMode.COUNTED.value,
        )
    )
    if change.action is ChangeAction.DELETE:
        if row is not None:
            await session.delete(row)
        return
    if row is None and change.action is not ChangeAction.CREATE:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id")
    await _write_pool(session, change, row, PoolMode.COUNTED, name)


async def write_legacy_pool(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a group's legacy pool (it is never deleted by a document)."""
    group = await _group_row(session, change.address[1])
    row = await session.scalar(select(CapacityPoolRow).where(CapacityPoolRow.group_id == group.id))
    if row is None and change.action is not ChangeAction.CREATE:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id")
    await _write_pool(session, change, row, PoolMode.LEGACY_GROUP, str(change.values["name"]))


async def _group_row(session: AsyncSession, slug: str) -> GroupRow:
    """Return the group row with ``slug``."""
    return await row_or_refuse(
        session, select(GroupRow).where(GroupRow.slug == slug), ErrorCode.GROUP_NOT_FOUND, "group"
    )


async def _flow_row(session: AsyncSession, change: Change) -> ServiceFlowRow | None:
    """Return the flow row a change addresses (organisation, group, code), if there is one."""
    group = await _group_row(session, change.address[1])
    return await session.scalar(
        select(ServiceFlowRow).where(
            ServiceFlowRow.group_id == group.id, ServiceFlowRow.code == change.address[2]
        )
    )


async def write_service_flow(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a flow: create it, change its state or pointers, or delete it.

    Changing which revision is the draft removes any other unpublished revision, so a replaced
    draft leaves nothing orphaned; a published revision is never touched here.
    """
    row = await _flow_row(session, change) if change.action is not ChangeAction.CREATE else None
    if change.action is ChangeAction.DELETE:
        if row is not None:
            await session.delete(row)
        return
    names, values = _names(change), change.values
    if change.action is ChangeAction.CREATE:
        group = await _group_row(session, change.address[1])
        row = ServiceFlowRow(group_id=group.id, code=change.key[0], state="draft")
        session.add(row)
    elif row is None:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id")
    if "state" in names:
        row.state = str(values["state"])
    if "default_for" in names:
        modes = set(_list(values, "default_for"))
        row.default_appointment = IntakeMode.APPOINTMENT.value in modes
        row.default_on_demand = IntakeMode.ON_DEMAND.value in modes
    for name in ("current_revision", "draft_revision"):
        if name in names:
            number = values.get(name)
            setattr(row, name, number if isinstance(number, int) else None)
    await session.flush()
    if "draft_revision" in names:
        await session.execute(
            delete(FlowRevisionRow).where(
                FlowRevisionRow.flow_id == row.id,
                FlowRevisionRow.published_at.is_(None),
                FlowRevisionRow.revision != (row.draft_revision or -1),
            )
        )


async def _pool_id(
    session: AsyncSession, change: Change, values: Mapping[str, Value]
) -> int | None:
    """Return the id of the pool a revision's fields name (counted by name, legacy by group)."""
    name, legacy_of = values.get("capacity_pool"), values.get("legacy_pool_of")
    if name is not None:
        found = await session.scalar(
            select(CapacityPoolRow.id).where(
                CapacityPoolRow.organisation_id
                == await find_organisation_id(session, change.address[0]),
                CapacityPoolRow.name == str(name),
                CapacityPoolRow.mode == PoolMode.COUNTED.value,
            )
        )
    elif legacy_of is not None:
        found = await session.scalar(
            select(CapacityPoolRow.id)
            .join(GroupRow, GroupRow.id == CapacityPoolRow.group_id)
            .where(GroupRow.slug == str(legacy_of))
        )
    else:
        return None
    if found is None:
        raise invalid("capacity_pool_id")
    return int(found)


def _subjects(values: Mapping[str, Value]) -> dict[str, list[str]]:
    """Return a revision's subjects (code to skill codes) from its change."""
    subjects = values.get("subjects")
    result: dict[str, list[str]] = {}
    for code, skills in subjects.items() if isinstance(subjects, dict) else ():
        result[code] = (
            [item for item in skills if isinstance(item, str)] if isinstance(skills, list) else []
        )
    return result


def _column(value: Value) -> str | None:
    """Return an optional text of a change as the column holds it."""
    return None if value is None else str(value)


async def write_flow_revision(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a revision that is new or an unpublished draft.

    Raises:
        DomainError: ``configuration_revision_published`` for a published revision, which is
            history (the planner refuses it first; this is the second line).
    """
    flow = await _flow_row(session, change)
    if flow is None:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id")
    number = int(change.key[0])
    row = await session.get(FlowRevisionRow, (flow.id, number))
    if row is not None and row.published_at is not None:
        raise DomainError(ErrorCode.CONFIGURATION_REVISION_PUBLISHED, "revision")
    if change.action is ChangeAction.DELETE:
        if row is not None:
            await session.delete(row)
        return
    values = change.values
    if row is None:
        row = FlowRevisionRow(flow_id=flow.id, revision=number)
        session.add(row)
    else:
        for child in (SubjectFlowSkillRow, SubjectFlowRow, FlowRevisionSkillRow):
            await session.execute(
                delete(child).where(child.flow_id == flow.id, child.revision == number)
            )
    labels = values.get("labels")
    published = values.get("published_at")
    row.labels = json.dumps(labels if isinstance(labels, dict) else {}, sort_keys=True)
    row.intake_modes = ",".join(sorted(_list(values, "intake_modes")))
    row.interaction_kind = _column(values.get("interaction_kind"))
    duration = values.get("duration_minutes")
    row.duration_minutes = duration if isinstance(duration, int) else None
    row.capacity_pool_id = await _pool_id(session, change, values)
    row.dispatch = _column(values.get("dispatch"))
    row.handling_action = _column(values.get("handling_action"))
    row.completion = _column(values.get("completion"))
    row.collects_email = values.get("collects_email") is not False
    row.legacy = values.get("legacy") is True
    row.published_at = datetime.fromisoformat(str(published)) if published is not None else None
    await session.flush()
    await _write_skills(session, flow.id, number, change, values)


async def _write_skills(
    session: AsyncSession, flow_id: int, number: int, change: Change, values: Mapping[str, Value]
) -> None:
    """Write the skills a revision requires and the subjects it offers with theirs."""
    ids = await _skill_ids(session, await find_organisation_id(session, change.address[0]))
    required, subjects = _list(values, "required_skills"), _subjects(values)
    if not set(required).union(*subjects.values()) <= set(ids):
        raise invalid("required_skill_ids")
    session.add_all(
        FlowRevisionSkillRow(flow_id=flow_id, revision=number, skill_id=ids[code])
        for code in sorted(set(required))
    )
    session.add_all(
        SubjectFlowRow(flow_id=flow_id, revision=number, subject_code=code)
        for code in sorted(subjects)
    )
    await session.flush()
    session.add_all(
        SubjectFlowSkillRow(
            flow_id=flow_id, revision=number, subject_code=code, skill_id=ids[skill]
        )
        for code, skills in sorted(subjects.items())
        for skill in sorted(set(skills))
    )
    await session.flush()


WRITERS: dict[str, Writer] = {
    SKILL_KIND: write_skill,
    CAPACITY_POOL_KIND: write_capacity_pool,
    LEGACY_POOL_KIND: write_legacy_pool,
    SERVICE_FLOW_KIND: write_service_flow,
    FLOW_REVISION_KIND: write_flow_revision,
}
"""The writers of this module, by the name of the kind they apply."""
