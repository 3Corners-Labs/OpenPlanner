"""Skills, capacity pools and flows in a configuration document (ADR 0121 step 2b-ii).

``read_catalogue`` and ``read_flows`` turn the nine tables of migration 0109 into the ``Entity``
objects the version-1 format describes (``app.domain.configuration_v1_skills`` and
``app.domain.configuration_v1_flows``): every reference is a natural key (a skill or a counted
pool by code, a legacy pool by its group's slug, a revision by number) and no id and no staff
identity leaves the database. ``WRITERS`` apply one planned ``Change`` inside the caller's
transaction, as the other ``config_*`` modules do: no audit event, no settings version, no SQL
outside the ORM.

The writers are not a second implementation of the screens' writes. A document is resolved to
ids here (a code to the id of the skill or pool, the group's slug to the group), the domain
objects are built (``Skill``, ``CapacityPool``, ``FlowRevision``) and the very writers of the
portal's adapters store them (``write_skill``, ``write_pool``, ``write_revision``). What stays
here is what the portal paths do not do: no scope filter, the archive flags and flow pointers a
document states, and the order of the flow changes.

Which person holds which skill (``staff_skills``) is state, not configuration: it is neither read
nor written here, and no write here removes a grant, because a skill that a person holds counts as
used (``flow_usage``) and is archived instead of deleted. A published revision is never written
again: the planner refuses a change to one, and ``write_revision`` refuses it a second time.
"""

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.capacity_pools import write_pool
from app.adapters.db.config_common import (
    Writer,
    find_organisation_id,
    invalid,
    moment,
    row_or_refuse,
)
from app.adapters.db.flow_usage import (
    flows_kept,
    pools_referred_to,
    skills_referred_to,
)
from app.adapters.db.models import (
    CapacityPoolRow,
    CapacityPoolSkillRow,
    FlowRevisionRow,
    FlowRevisionSkillRow,
    GroupRow,
    OrganisationRow,
    ServiceFlowRow,
    SkillRow,
    SubjectFlowRow,
    SubjectFlowSkillRow,
)
from app.adapters.db.service_flows import (
    delete_flow_row,
    prune_unpublished_revisions,
    set_flow_defaults,
    write_flow_pointers,
    write_flow_state,
    write_revision,
)
from app.adapters.db.skills import skill_ids_by_code
from app.adapters.db.skills import write_skill as write_skill_row
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_checks import (
    json_list,
    json_texts,
    strings_of,
    text_map,
    text_of,
)
from app.domain.configuration_tree import Address, Entity
from app.domain.configuration_v1_flows import revision_of
from app.domain.configuration_v1_names import (
    CAPACITY_POOL_KIND,
    FLOW_REVISION_KIND,
    LEGACY_POOL_KIND,
    SERVICE_FLOW_KIND,
    SKILL_KIND,
)
from app.domain.configuration_values import Value
from app.domain.errors import DomainError, ErrorCode
from app.domain.service_flows import (
    FlowState,
    IntakeMode,
    check_revision_editable,
)
from app.domain.skills import Skill

type Sections = dict[str, tuple[Entity, ...]]
"""The collections a reader hands to the assembler: section name to its objects."""


# --- reading ------------------------------------------------------------------------------


async def read_catalogue(
    session: AsyncSession,
) -> tuple[dict[str, Sections], frozenset[str], dict[str, frozenset[str]]]:
    """Return every organisation's ``skills`` and counted ``capacity_pools``, by slug.

    A counted pool is addressed by its code. One whose stored code is missing (a row an older
    image wrote after migration 0110) has no address and is left out of the tree; the second
    result names it, as ``organisation/id``, so an export can refuse it, and the third gives the
    names of those that are not archived by organisation, so a document cannot create a second
    pool with a name that one still holds.
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
                fields={
                    "labels": json_texts(json.loads(row.labels)),
                    "archived": row.archived_at is not None,
                },
            )
        )
    codes = await _skill_codes(session)
    guaranteed = await _guaranteed(session, codes)
    pools: dict[str, list[Entity]] = defaultdict(list)
    without_code: set[str] = set()
    hidden_names: dict[str, set[str]] = defaultdict(set)
    for pool_row, organisation in (
        await session.execute(
            select(CapacityPoolRow, OrganisationRow.slug)
            .join(OrganisationRow, OrganisationRow.id == CapacityPoolRow.organisation_id)
            .where(CapacityPoolRow.mode == PoolMode.COUNTED.value)
            .order_by(OrganisationRow.slug, CapacityPoolRow.code, CapacityPoolRow.id)
        )
    ).all():
        if pool_row.code is None:
            without_code.add(f"{organisation}/{pool_row.id}")
            if pool_row.archived_at is None:
                hidden_names[organisation].add(pool_row.name)
            continue
        pools[organisation].append(
            Entity(
                CAPACITY_POOL_KIND,
                (pool_row.code,),
                fields={
                    "name": pool_row.name,
                    "guaranteed_skills": json_list(sorted(guaranteed[pool_row.id])),
                    "archived": pool_row.archived_at is not None,
                },
            )
        )
    sections = {
        slug: {
            name: tuple(found[slug])
            for name, found in (("skills", skills), ("capacity_pools", pools))
            if found.get(slug)
        }
        for slug in {*skills, *pools}
    }
    return (
        sections,
        frozenset(without_code),
        {slug: frozenset(names) for slug, names in hidden_names.items()},
    )


async def _skill_codes(session: AsyncSession) -> dict[int, str]:
    """Return the code of every skill by id."""
    return {
        skill_id: code
        for skill_id, code in (await session.execute(select(SkillRow.id, SkillRow.code))).all()
    }


async def _guaranteed(session: AsyncSession, codes: dict[int, str]) -> dict[int, list[str]]:
    """Return the codes of the skills every pool guarantees, by pool id."""
    guaranteed: dict[int, list[str]] = defaultdict(list)
    for pool_id, skill_id in (
        await session.execute(select(CapacityPoolSkillRow.pool_id, CapacityPoolSkillRow.skill_id))
    ).all():
        guaranteed[pool_id].append(codes[skill_id])
    return guaranteed


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
    guaranteed = await _guaranteed(session, codes)
    reference: dict[int, tuple[str, str | None]] = {}
    sections: dict[Address, Sections] = defaultdict(dict)
    for pool, organisation, group in pools:
        if group is None:
            reference[pool.id] = ("capacity_pool", pool.code)
            continue
        reference[pool.id] = ("legacy_pool_of", group)
        sections[(organisation, group)]["legacy_pool"] = (
            Entity(
                LEGACY_POOL_KIND,
                (),
                fields={
                    "name": pool.name,
                    "guaranteed_skills": json_list(sorted(guaranteed[pool.id])),
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
                    "default_for": json_list(sorted(modes)),
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
    session: AsyncSession, codes: dict[int, str], reference: dict[int, tuple[str, str | None]]
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
            "labels": json_texts(json.loads(row.labels)),
            "intake_modes": json_list(sorted(mode for mode in row.intake_modes.split(",") if mode)),
            "interaction_kind": row.interaction_kind,
            "duration_minutes": row.duration_minutes,
            "capacity_pool": None,
            "legacy_pool_of": None,
            "required_skills": json_list(sorted(required[key])),
            "subjects": {c: json_list(sorted(i)) for c, i in sorted(subjects[key].items())},
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


async def read_usage_of_flows(session: AsyncSession) -> dict[str, frozenset[tuple[str, ...]]]:
    """Return what a document archives instead of deleting (``Usage.kept``), by kind and address.

    A skill is in use when a person holds it, a pool guarantees it or any revision (old ones
    too) or subject requires it; a counted pool when any revision draws on it; a flow when a
    booking refers to it or it is or was active. Such an object is archived,
    never deleted, by a document that leaves it out or deletes it, because deleting it would
    silently remove history or grants. The definitions are in ``app.adapters.db.flow_usage``.
    """
    skills = await session.execute(
        select(OrganisationRow.slug, SkillRow.code)
        .join(OrganisationRow, OrganisationRow.id == SkillRow.organisation_id)
        .where(SkillRow.id.in_(skills_referred_to()))
    )
    pools = await session.execute(
        select(OrganisationRow.slug, CapacityPoolRow.code)
        .join(OrganisationRow, OrganisationRow.id == CapacityPoolRow.organisation_id)
        .where(
            CapacityPoolRow.mode == PoolMode.COUNTED.value,
            CapacityPoolRow.code.is_not(None),
            CapacityPoolRow.id.in_(pools_referred_to()),
        )
    )
    flows = await session.execute(
        select(OrganisationRow.slug, GroupRow.slug, ServiceFlowRow.code)
        .join(GroupRow, GroupRow.id == ServiceFlowRow.group_id)
        .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
        .where(ServiceFlowRow.id.in_(flows_kept()))
    )
    return {
        SKILL_KIND: frozenset((org, code) for org, code in skills.all()),
        CAPACITY_POOL_KIND: frozenset((org, code) for org, code in pools.all() if code is not None),
        SERVICE_FLOW_KIND: frozenset((org, group, code) for org, group, code in flows.all()),
    }


# --- writing ------------------------------------------------------------------------------


def _names(change: Change) -> set[str]:
    """Return the fields a change writes: all of them on create, else the named ones."""
    return set(change.values) if change.action is ChangeAction.CREATE else set(change.fields)


def _archived_at(was: datetime | None, archived: Value, change: Change) -> datetime | None:
    """Return the archive time a flag gives: kept when archived already, else when it happens.

    The time is the apply's own (``Change.at``, the service's clock), so archive times are
    deterministic and the portal's; a change that did not come through the service has none and
    falls back to the system clock.
    """
    if not archived:
        return None
    return was or change.at or datetime.now(UTC)


async def write_skill(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a skill: create it, change or archive it, or delete it."""
    organisation_id = await find_organisation_id(session, change.address[0])
    code = change.key[0]
    row = await session.scalar(
        select(SkillRow).where(SkillRow.organisation_id == organisation_id, SkillRow.code == code)
    )
    if change.action is ChangeAction.DELETE:
        if row is not None:
            await session.delete(row)
        return
    if row is None and change.action is not ChangeAction.CREATE:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_id")
    skill = Skill(
        None if row is None else row.id,
        organisation_id,
        code,
        text_map(change.values.get("labels"), "skill_label"),
    )
    row = await write_skill_row(session, skill, row)
    if "archived" in _names(change):
        row.archived_at = _archived_at(row.archived_at, change.values.get("archived"), change)
    await session.flush()


async def _guaranteed_ids(
    session: AsyncSession, organisation_id: int, values: Mapping[str, Value]
) -> frozenset[int]:
    """Return the ids of the skills a pool change guarantees.

    Raises:
        DomainError: ``invalid_configuration`` for a code that is no skill of the organisation.
    """
    ids = await skill_ids_by_code(session, organisation_id)
    wanted = set(strings_of(values, "guaranteed_skills"))
    if not wanted <= set(ids):
        raise invalid("guaranteed_skill_ids")
    return frozenset(ids[code] for code in wanted)


async def _write_pool(
    session: AsyncSession, change: Change, row: CapacityPoolRow | None, pool: CapacityPool
) -> None:
    """Store a pool through the portal's writer, then its archive flag as the change states it."""
    row = await write_pool(session, pool, row)
    if "archived" in _names(change):
        row.archived_at = _archived_at(row.archived_at, change.values.get("archived"), change)
    await session.flush()


async def write_capacity_pool(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a counted pool, addressed by its code within the organisation."""
    organisation_id = await find_organisation_id(session, change.address[0])
    code = change.key[0]
    row = await session.scalar(
        select(CapacityPoolRow).where(
            CapacityPoolRow.organisation_id == organisation_id,
            CapacityPoolRow.code == code,
            CapacityPoolRow.mode == PoolMode.COUNTED.value,
        )
    )
    if change.action is ChangeAction.DELETE:
        if row is not None:
            await session.delete(row)
        return
    if row is None and change.action is not ChangeAction.CREATE:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id")
    pool = CapacityPool(
        None if row is None else row.id,
        organisation_id,
        text_of(change.values, "name"),
        PoolMode.COUNTED,
        await _guaranteed_ids(session, organisation_id, change.values),
        code=code,
    )
    await _write_pool(session, change, row, pool)


async def write_legacy_pool(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a group's legacy pool (it is never deleted by a document)."""
    group = await _group_row(session, change.address[1])
    row = await session.scalar(select(CapacityPoolRow).where(CapacityPoolRow.group_id == group.id))
    if row is None and change.action is not ChangeAction.CREATE:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "pool_id")
    pool = CapacityPool(
        None if row is None else row.id,
        group.organisation_id,
        text_of(change.values, "name"),
        PoolMode.LEGACY_GROUP,
        await _guaranteed_ids(session, group.organisation_id, change.values),
        group_id=group.id,
    )
    await _write_pool(session, change, row, pool)


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


def _is_default_path_clash(error: IntegrityError) -> bool:
    """Whether a unique violation is the one of ``uq_service_flows_default_*``.

    SQLite names the columns, not the index: the default indexes are on ``group_id`` alone, the
    code constraint on ``group_id`` and ``code``.
    """
    message = str(error.orig)
    return "service_flows.group_id" in message and "service_flows.code" not in message


def _pointer(
    values: Mapping[str, Value], stored: int | None, name: str, names: set[str]
) -> int | None:
    """Return a flow pointer after the change: the document's value when it states one."""
    if name not in names:
        return stored
    value = values.get(name)
    return value if isinstance(value, int) else None


async def write_service_flow(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a flow: create it, change its state or pointers, or delete it.

    Changing which revision is the draft removes any other unpublished revision, so a replaced
    draft leaves nothing orphaned; a published revision is never touched here.

    Raises:
        DomainError: ``configuration_object_exists`` (``default_for``) when another active flow
            of the group is the default of an entry path this one becomes the default of, and
            what the pointers' rows refuse.
    """
    row = await _flow_row(session, change) if change.action is not ChangeAction.CREATE else None
    if change.action is ChangeAction.DELETE:
        if row is not None:
            await delete_flow_row(session, row)
        return
    names, values = _names(change), change.values
    if change.action is ChangeAction.CREATE:
        group = await _group_row(session, change.address[1])
        row = ServiceFlowRow(group_id=group.id, code=change.key[0], state="draft")
        session.add(row)
    elif row is None:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_MISSING, "flow_id")
    if "state" in names:
        write_flow_state(row, text_of(values, "state"))
    if "default_for" in names:
        set_flow_defaults(row, [IntakeMode(mode) for mode in strings_of(values, "default_for")])
    if names & {"current_revision", "draft_revision"}:
        write_flow_pointers(
            row,
            current_revision=_pointer(values, row.current_revision, "current_revision", names),
            draft_revision=_pointer(values, row.draft_revision, "draft_revision", names),
        )
    try:
        await session.flush()
    except IntegrityError as error:
        if _is_default_path_clash(error):
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_EXISTS, "default_for") from error
        raise
    if "draft_revision" in names:
        await prune_unpublished_revisions(session, row.id, row.draft_revision)


async def _pool_id(
    session: AsyncSession, change: Change, values: Mapping[str, Value]
) -> int | None:
    """Return the id of the pool a revision's fields name (counted by code, legacy by group).

    A group's legacy pool is the pool of the flow's own group and no other: the second line of
    defence of the rule of the planner.

    Raises:
        DomainError: ``invalid_configuration`` for a pool that is not there or not the flow's own.
    """
    code, legacy_of = values.get("capacity_pool"), values.get("legacy_pool_of")
    if code is not None:
        found = await session.scalar(
            select(CapacityPoolRow.id).where(
                CapacityPoolRow.organisation_id
                == await find_organisation_id(session, change.address[0]),
                CapacityPoolRow.code == str(code),
                CapacityPoolRow.mode == PoolMode.COUNTED.value,
            )
        )
    elif legacy_of is not None:
        if legacy_of != change.address[1]:
            raise invalid("legacy_pool_of")
        found = await session.scalar(
            select(CapacityPoolRow.id)
            .join(GroupRow, GroupRow.id == CapacityPoolRow.group_id)
            .where(GroupRow.slug == change.address[1])
        )
    else:
        return None
    if found is None:
        raise invalid("capacity_pool_id")
    return int(found)


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
    check_revision_editable(None if row is None else row.published_at)
    if change.action is ChangeAction.DELETE:
        if row is not None:
            await session.delete(row)
        return
    ids = await skill_ids_by_code(session, await find_organisation_id(session, change.address[0]))

    def skill_id(code: str, field: str) -> int:
        if code not in ids:
            raise invalid(field)
        return ids[code]

    revision = revision_of(
        change.values,
        number,
        flow_id=flow.id,
        pool_id=await _pool_id(session, change, change.values),
        skill_id=skill_id,
    )
    await write_revision(session, flow.id, number, revision)


def _releases(change: Change) -> bool:
    """Whether a flow change gives up nothing: it is not active with a default entry path."""
    values = change.values
    gains = (
        change.action in (ChangeAction.CREATE, ChangeAction.UPDATE)
        and values.get("state") == FlowState.ACTIVE.value
        and bool(values.get("default_for"))
        and (change.action is ChangeAction.CREATE or {"state", "default_for"} & set(change.fields))
    )
    return not gains


def order_flow_changes(changes: Sequence[Change]) -> list[Change]:
    """Return the changes with the flows of each group written releasing before gaining.

    Of a group's flows, at most one active flow is the default of an entry path (a partial unique
    index). A document that moves a default, or archives the flow that held it, would break that
    index halfway if the flow that gains were written first, so within a run of the flows of one
    group the flows that are not active with a default (archived, no longer default, draft) come
    first; the order is otherwise the plan's, and each flow keeps its revisions behind it.
    Everything else keeps its place.
    """
    blocks: list[list[Change]] = []
    for change in changes:
        if change.kind == FLOW_REVISION_KIND and blocks and blocks[-1][0].kind == SERVICE_FLOW_KIND:
            blocks[-1].append(change)
        else:
            blocks.append([change])
    ordered: list[Change] = []
    run: list[list[Change]] = []

    def flush() -> None:
        ordered.extend(
            change for block in sorted(run, key=lambda b: not _releases(b[0])) for change in block
        )
        run.clear()

    for block in blocks:
        head = block[0]
        if head.kind == SERVICE_FLOW_KIND and (
            not run or run[0][0].address[:2] == head.address[:2]
        ):
            run.append(block)
            continue
        flush()
        if head.kind == SERVICE_FLOW_KIND:
            run.append(block)
        else:
            ordered.extend(block)
    flush()
    return ordered


WRITERS: dict[str, Writer] = {
    SKILL_KIND: write_skill,
    CAPACITY_POOL_KIND: write_capacity_pool,
    LEGACY_POOL_KIND: write_legacy_pool,
    SERVICE_FLOW_KIND: write_service_flow,
    FLOW_REVISION_KIND: write_flow_revision,
}
"""The writers of this module, by the name of the kind they apply."""
