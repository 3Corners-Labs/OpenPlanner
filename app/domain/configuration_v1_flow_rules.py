"""The rules across the skills, pools and flows of a configuration document (ADR 0121 2b-ii).

A document names everything by natural key, so a rule here first reads the tree a document would
leave behind into a small *world*: the skills, pools and groups of every organisation with ids
that are only there for one run. Then it calls the very functions the admin screens call
(``check_pool_save``, ``check_pool_archive``, ``check_flow_references``, ``check_default_paths``,
``check_state_change``, ``readiness``, the ``ServiceFlow`` and ``FlowRevision`` constructors), so
a document cannot make a flow use another organisation's pool or skill, activate two defaults for
one entry path, name an unknown subject or set a location.

Only what the plan creates or changes is checked, as the portal checks what an admin changes:
what is stored and left alone stays valid. History is read leniently on purpose: a published
revision may name a skill or pool that was archived afterwards or a subject that is gone, so an
export always imports again; a *draft* is checked as strictly as a save in the portal.

Two rules keep the new sections from being a way around the screens. A document activates a
flow only when the readiness checklist passes, so it cannot bring a flow live that the checklist
refuses today (every value outside the ``BUILT_*`` constants of ``service_flows``). A revision
marked ``legacy`` must be exactly the wrapper the backfill writes (``legacy_revision``), on the
group's own legacy pool, so the flag cannot skip those tests either.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import count

from app.domain.capacity import CapacityPool, PoolMode, check_pool_archive, check_pool_save
from app.domain.configuration_changes import (
    Change,
    ChangeAction,
    IntegrityRule,
    Refusal,
    Resolution,
)
from app.domain.configuration_checks import strings_of, text_of
from app.domain.configuration_tree import Address, Entity, path_of, walk
from app.domain.configuration_v1_names import (
    CAPACITY_POOL_KIND,
    FLOW_REVISION_KIND,
    GROUP_KIND,
    LEGACY_POOL_KIND,
    ORGANISATION_KIND,
    SERVICE_FLOW_KIND,
    SKILL_KIND,
)
from app.domain.configuration_values import Value
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.routing_codec import routing_from_json
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ServiceFlow,
    can_activate,
    check_default_paths,
    check_flow_references,
    check_state_change,
    legacy_revision,
    readiness,
)
from app.domain.skills import Skill

_ARCHIVED = datetime(1970, 1, 1, tzinfo=UTC)
"""Stands for "archived at some time" in the objects of a run; the time itself never matters."""
_POOLS = (CAPACITY_POOL_KIND, LEGACY_POOL_KIND)
_WRITES = (ChangeAction.CREATE, ChangeAction.UPDATE, ChangeAction.ARCHIVE)


@dataclass(frozen=True)
class _World:
    """The skills, pools and groups of a tree, by natural key, with ids for this run only.

    Attributes:
        organisations: Id of every organisation, by slug.
        groups: Id of every group, by slug (slugs are unique across the installation).
        group_organisation: The organisation slug of every group.
        skills: Every skill by ``(organisation, code)``.
        pools: Every counted pool by ``(organisation, name)``.
        legacy: Every group's legacy pool, by the group's slug.
        subjects: The subject codes every group offers, by slug.
    """

    organisations: Mapping[str, int]
    groups: Mapping[str, int]
    group_organisation: Mapping[str, str]
    skills: Mapping[tuple[str, str], Skill]
    pools: Mapping[tuple[str, str], CapacityPool]
    legacy: Mapping[str, CapacityPool]
    subjects: Mapping[str, frozenset[str]]

    @property
    def skills_by_id(self) -> dict[int, Skill]:
        """Every skill by its id."""
        return {skill.id: skill for skill in self.skills.values() if skill.id is not None}

    def skill_id(self, organisation: str, code: str, field: str) -> int:
        """Return the id of the organisation's skill ``code``.

        Raises:
            DomainError: ``configuration_object_missing`` naming ``field``.
        """
        skill = self.skills.get((organisation, code))
        if skill is None or skill.id is None:
            raise missing_object(field)
        return skill.id

    def pool_of(self, organisation: str, fields: Mapping[str, Value]) -> CapacityPool | None:
        """Return the pool a revision's fields name, ``None`` when it names none.

        Raises:
            DomainError: ``configuration_object_missing`` for a pool that is not in the tree.
        """
        name, legacy_of = fields.get("capacity_pool"), fields.get("legacy_pool_of")
        if name is None and legacy_of is None:
            return None
        found = (
            self.pools.get((organisation, str(name)))
            if name is not None
            else self.legacy.get(str(legacy_of))
        )
        if found is None:
            raise missing_object("capacity_pool_id")
        return found


def _index(root: Entity, kind: str) -> dict[Address, Entity]:
    """Return every object of ``kind`` below ``root`` by address."""
    return dict(walk(root, kind))


def _ids(
    world: Mapping[tuple[str, str], Skill], organisation: str, codes: list[str]
) -> frozenset[int]:
    """Return the ids of the skills with these codes; a code without a skill is left out."""
    return frozenset(
        skill.id
        for code in codes
        if (skill := world.get((organisation, code))) is not None and skill.id is not None
    )


def _pool(
    entity: Entity,
    pool_id: int,
    organisation_id: int,
    name: str,
    skills: Mapping[tuple[str, str], Skill],
    address: Address,
    group_id: int | None,
) -> CapacityPool | None:
    """Build the pool an object describes; ``None`` when it cannot be built."""
    try:
        return CapacityPool(
            pool_id,
            organisation_id,
            name,
            PoolMode.COUNTED if group_id is None else PoolMode.LEGACY_GROUP,
            _ids(skills, address[0], strings_of(entity.fields, "guaranteed_skills")),
            _ARCHIVED if entity.fields.get("archived") else None,
            group_id,
        )
    except DomainError:
        return None


def _world(root: Entity) -> _World:
    """Read the skills, pools and groups of a tree; an object that cannot be built is left out."""
    ids = count(1)
    organisations = {address[0]: next(ids) for address, _ in walk(root, ORGANISATION_KIND)}
    skills: dict[tuple[str, str], Skill] = {}
    for address, entity in walk(root, SKILL_KIND):
        labels = entity.fields.get("labels")
        try:
            skills[(address[0], address[1])] = Skill(
                next(ids),
                organisations[address[0]],
                address[1],
                labels if isinstance(labels, dict) else {},  # type: ignore[arg-type]
                _ARCHIVED if entity.fields.get("archived") else None,
            )
        except DomainError:
            continue
    groups: dict[str, int] = {}
    group_organisation: dict[str, str] = {}
    subjects: dict[str, frozenset[str]] = {}
    for address, group in walk(root, GROUP_KIND):
        groups[address[1]] = next(ids)
        group_organisation[address[1]] = address[0]
        try:
            offered = routing_from_json(group.fields.get("routing")).subjects
        except DomainError:
            offered = ()
        subjects[address[1]] = frozenset(subject.code for subject in offered)
    pools: dict[tuple[str, str], CapacityPool] = {}
    legacy: dict[str, CapacityPool] = {}
    for address, pool in walk(root, CAPACITY_POOL_KIND):
        built = _pool(pool, next(ids), organisations[address[0]], address[1], skills, address, None)
        if built is not None:
            pools[(address[0], address[1])] = built
    for address, pool in walk(root, LEGACY_POOL_KIND):
        name = text_of(pool.fields, "name")
        built = _pool(
            pool,
            next(ids),
            organisations[address[0]],
            name,
            skills,
            address,
            groups.get(address[1]),
        )
        if built is not None:
            legacy[address[1]] = built
    return _World(organisations, groups, group_organisation, skills, pools, legacy, subjects)


def _refusal(path: str, error: DomainError) -> Refusal:
    """Return the refusal a domain error makes at ``path``."""
    return Refusal(path, error.code, error.field)


# --- pools -------------------------------------------------------------------------------


def _pool_rules(resolution: Resolution) -> Iterator[Refusal]:
    """A pool guarantees only its organisation's own skills, and is not archived under a flow."""
    world = _world(resolution.resulting)
    before = {kind: _index(resolution.current, kind) for kind in _POOLS}
    for change in resolution.changes:
        if change.kind not in _POOLS or change.action not in _WRITES:
            continue
        entity = _index(resolution.resulting, change.kind).get(change.address)
        pool = (
            world.pools.get((change.address[0], change.address[1]))
            if change.kind == CAPACITY_POOL_KIND
            else world.legacy.get(change.address[1])
        )
        if entity is None or pool is None:
            continue
        organisation = change.address[0]
        codes = strings_of(entity.fields, "guaranteed_skills")
        if any((organisation, code) not in world.skills for code in codes):
            yield Refusal(
                change.path, ErrorCode.CONFIGURATION_OBJECT_MISSING, "guaranteed_skill_ids"
            )
            continue
        old = before[change.kind].get(change.address)
        already = (
            _ids(world.skills, organisation, strings_of(old.fields, "guaranteed_skills"))
            if old
            else frozenset()
        )
        try:
            check_pool_save(
                replace(pool, id=None), None, pool.organisation_id, world.skills_by_id, already
            )
        except DomainError as error:
            yield _refusal(change.path, error)
            continue
        if pool.archived_at is not None and (
            change.action is ChangeAction.ARCHIVE or "archived" in change.fields
        ):
            try:
                check_pool_archive(_drawing(resolution.resulting, change))
            except DomainError as error:
                yield _refusal(change.path, error)


def _drawing(root: Entity, change: Change) -> list[str]:
    """Return the codes of the flows, not archived, whose current or draft revision uses a pool."""
    organisation = change.address[0]
    codes: set[str] = set()
    for address, flow in walk(root, SERVICE_FLOW_KIND):
        if address[0] != organisation or flow.fields.get("state") == FlowState.ARCHIVED.value:
            continue
        in_effect = {flow.fields.get("current_revision"), flow.fields.get("draft_revision")}
        for revision in flow.children.get("revisions", ()):
            if int(revision.key[0]) not in in_effect:
                continue
            uses = (
                revision.fields.get("capacity_pool") == change.address[1]
                if change.kind == CAPACITY_POOL_KIND
                else revision.fields.get("legacy_pool_of") == change.address[1]
            )
            if uses:
                codes.add(address[2])
    return sorted(codes)


# --- flows -------------------------------------------------------------------------------


def _subjects(fields: Mapping[str, Value]) -> dict[str, Value]:
    """Return a revision's subjects as the object holds them (code to a list of skill codes)."""
    subjects = fields.get("subjects")
    return dict(subjects) if isinstance(subjects, dict) else {}


def _codes(value: Value) -> list[str]:
    """Return the texts of a list value."""
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _revision_of(world: _World, address: Address, revision: Entity) -> FlowRevision:
    """Build the revision an object describes, with the ids of this run.

    Raises:
        DomainError: ``configuration_object_missing`` for a skill or pool the tree does not hold,
            and what the ``FlowRevision`` constructor refuses.
    """
    organisation, fields = address[0], revision.fields
    pool = world.pool_of(organisation, fields)
    required = frozenset(
        world.skill_id(organisation, code, "required_skill_ids")
        for code in strings_of(fields, "required_skills")
    )
    extra = {
        code: frozenset(
            world.skill_id(organisation, c, "subject_skill_ids") for c in _codes(skills)
        )
        for code, skills in _subjects(fields).items()
    }
    labels = fields.get("labels")
    published = fields.get("published_at")
    kind, action = fields.get("interaction_kind"), fields.get("handling_action")
    dispatch, completion = fields.get("dispatch"), fields.get("completion")
    duration = fields.get("duration_minutes")
    return FlowRevision(
        flow_id=None,
        revision=int(revision.key[0]),
        labels=labels if isinstance(labels, dict) else {},  # type: ignore[arg-type]
        intake_modes=frozenset(IntakeMode(m) for m in strings_of(fields, "intake_modes")),
        interaction_kind=InteractionKind(str(kind)) if kind is not None else None,
        duration_minutes=duration if isinstance(duration, int) else None,
        capacity_pool_id=pool.id if pool is not None else None,
        required_skill_ids=required,
        subject_skill_ids=extra,
        dispatch=DispatchKind(str(dispatch)) if dispatch is not None else None,
        handling_action=HandlingAction(str(action)) if action is not None else None,
        completion=CompletionPolicy(str(completion)) if completion is not None else None,
        collects_email=fields.get("collects_email") is not False,
        legacy=fields.get("legacy") is True,
        published_at=datetime.fromisoformat(str(published)) if published is not None else None,
    )


def _revisions_of(flow: Entity) -> dict[int, Entity]:
    """Return the revisions a flow object holds by number."""
    return {int(revision.key[0]): revision for revision in flow.children.get("revisions", ())}


def _number(value: Value) -> int | None:
    """Return a revision number a flow field holds, ``None`` when it holds none."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flow_object(world: _World, address: Address, flow: Entity) -> ServiceFlow:
    """Build the flow an object describes from its current and draft revision.

    Raises:
        DomainError: What building a revision or the ``ServiceFlow`` refuses.
    """
    revisions = _revisions_of(flow)
    current, draft = (
        _number(flow.fields.get("current_revision")),
        _number(flow.fields.get("draft_revision")),
    )
    return ServiceFlow(
        None,
        world.groups[address[1]],
        address[2],
        FlowState(str(flow.fields.get("state"))),
        frozenset(IntakeMode(m) for m in strings_of(flow.fields, "default_for")),
        _revision_of(world, address, revisions[current]) if current in revisions else None,
        _revision_of(world, address, revisions[draft]) if draft in revisions else None,
    )


def _legacy_shape(world: _World, address: Address, revision: FlowRevision) -> None:
    """Refuse a revision marked legacy that is not exactly the wrapper the backfill writes.

    Raises:
        DomainError: ``invalid_configuration`` naming ``legacy``.
    """
    own = world.legacy.get(address[1])
    if (
        len(revision.intake_modes) != 1
        or revision.duration_minutes is None
        or revision.dispatch is None
        or revision.published_at is None
        or own is None
        or revision.capacity_pool_id != own.id
    ):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "legacy")
    expected = replace(
        legacy_revision(
            labels=revision.labels,
            intake_mode=next(iter(revision.intake_modes)),
            duration_minutes=revision.duration_minutes,
            capacity_pool_id=own.id or 0,
            dispatch=revision.dispatch,
        ),
        revision=revision.revision,
        published_at=revision.published_at,
    )
    if expected != revision:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "legacy")


def _already(
    world: _World, address: Address, old: Entity | None
) -> tuple[frozenset[int], frozenset[int]]:
    """Return the skill ids and pool ids the flow's revisions in effect hold in the current tree."""
    skills: set[int] = set()
    pools: set[int] = set()
    if old is None:
        return frozenset(), frozenset()
    in_effect = {
        _number(old.fields.get("current_revision")),
        _number(old.fields.get("draft_revision")),
    }
    for number, revision in _revisions_of(old).items():
        if number not in in_effect:
            continue
        fields = revision.fields
        skills |= _ids(world.skills, address[0], strings_of(fields, "required_skills"))
        for extra in _subjects(fields).values():
            skills |= _ids(world.skills, address[0], _codes(extra))
        try:
            pool = world.pool_of(address[0], fields)
        except DomainError:
            pool = None
        if pool is not None and pool.id is not None:
            pools.add(pool.id)
    return frozenset(skills), frozenset(pools)


def _flow_path(address: Address) -> str:
    """Return the path of a flow in a document."""
    group = path_of(path_of("", "organisations", address[:1]), "groups", address[1:2])
    return path_of(group, "flows", address[2:3])


def _check_revision(
    world: _World,
    address: Address,
    flow: Entity,
    number: int,
    old: Entity | None,
) -> Iterator[Refusal]:
    """Check one revision the plan writes: its place in the flow, references, legacy shape."""
    revision = _revisions_of(flow)[number]
    path = path_of(_flow_path(address), "revisions", (str(number),))
    published = revision.fields.get("published_at") is not None
    if not published and number != _number(flow.fields.get("draft_revision")):
        yield Refusal(path, ErrorCode.INVALID_CONFIGURATION, "published_at")
        return
    if published and number == _number(flow.fields.get("draft_revision")):
        yield Refusal(path, ErrorCode.INVALID_CONFIGURATION, "published_at")
        return
    try:
        built = _revision_of(world, address, revision)
        if built.legacy:
            _legacy_shape(world, address, built)
        organisation = world.organisations[address[0]]
        pool = world.pool_of(address[0], revision.fields)
        offered = world.subjects.get(address[1], frozenset())
        if published:
            required, already_pool = built.all_skill_ids, built.capacity_pool_id
            offered = offered | frozenset(built.subject_skill_ids)
        else:
            required, pools = _already(world, address, old)
            already_pool = built.capacity_pool_id if built.capacity_pool_id in pools else None
        check_flow_references(
            organisation,
            built,
            pool,
            world.skills_by_id,
            required,
            offered,
            already_pool_id=already_pool,
        )
    except DomainError as error:
        yield _refusal(path, error)


def _flow_rules(resolution: Resolution) -> Iterator[Refusal]:
    """Check every flow the plan writes: its revisions, its state and the checklist."""
    world = _world(resolution.resulting)
    flows = _index(resolution.resulting, SERVICE_FLOW_KIND)
    before = _index(resolution.current, SERVICE_FLOW_KIND)
    touched: dict[Address, set[int]] = {}
    for change in resolution.changes:
        if change.kind == SERVICE_FLOW_KIND and change.action in _WRITES:
            touched.setdefault(change.address, set())
        if change.kind == FLOW_REVISION_KIND and change.action in _WRITES[:2]:
            touched.setdefault(change.address[:3], set()).add(int(change.key[0]))
    for address in sorted(touched):
        flow = flows.get(address)
        if flow is None:
            continue
        yield from _check_flow(world, address, flow, before.get(address), touched[address])


def _check_flow(
    world: _World, address: Address, flow: Entity, old: Entity | None, written: set[int]
) -> Iterator[Refusal]:
    """Check one flow: the revisions it names, the ones written, its state and readiness."""
    path = _flow_path(address)
    revisions = _revisions_of(flow)
    current, draft = (_number(flow.fields.get(n)) for n in ("current_revision", "draft_revision"))
    for name, number in (("current_revision", current), ("draft_revision", draft)):
        if number is not None and number not in revisions:
            yield Refusal(path, ErrorCode.CONFIGURATION_OBJECT_MISSING, name)
    if current in revisions and revisions[current].fields.get("published_at") is None:
        yield Refusal(path, ErrorCode.INVALID_CONFIGURATION, "published_at")
    refused = [
        r
        for number in sorted(written & set(revisions))
        for r in _check_revision(world, address, flow, number, old)
    ]
    yield from refused
    if refused:
        return
    try:
        built = _flow_object(world, address, flow)
    except DomainError as error:
        yield _refusal(path, error)
        return
    if old is not None and old.fields.get("state") != flow.fields.get("state"):
        try:
            check_state_change(
                replace(built, state=FlowState(str(old.fields.get("state")))), built.state
            )
        except DomainError as error:
            yield _refusal(path, error)
    if built.state is FlowState.ACTIVE and built.current is not None:
        pool = _pool_of(world, address, built.current)
        items = readiness(built.current, pool, offered_subjects=world.subjects.get(address[1], ()))
        if not can_activate(items):
            for item in items:
                if not item.ok:
                    yield Refusal(
                        path, ErrorCode.INVALID_CONFIGURATION, f"readiness_{item.code.value}"
                    )


def _pool_of(world: _World, address: Address, revision: FlowRevision) -> CapacityPool | None:
    """Return the pool of this run a built revision draws on."""
    candidates = (*world.pools.values(), *world.legacy.values())
    return next((p for p in candidates if p.id == revision.capacity_pool_id), None)


def _default_rules(resolution: Resolution) -> Iterator[Refusal]:
    """No two active flows of a group are the default of the same entry path."""
    world = _world(resolution.resulting)
    groups = {
        c.address[:2]
        for c in resolution.changes
        if c.kind in (SERVICE_FLOW_KIND, FLOW_REVISION_KIND)
    }
    active: dict[Address, ServiceFlow] = {}
    for address, entity in walk(resolution.resulting, SERVICE_FLOW_KIND):
        if address[:2] in groups and entity.fields.get("state") == FlowState.ACTIVE.value:
            try:
                active[address] = _flow_object(world, address, entity)
            except DomainError:
                continue
    for address, flow in sorted(active.items()):
        others = [
            mode
            for other, found in active.items()
            if other != address and other[:2] == address[:2]
            for mode in found.default_for
        ]
        try:
            check_default_paths(flow, others)
        except DomainError as error:
            yield _refusal(_flow_path(address), error)


def _immutable_rules(resolution: Resolution) -> Iterator[Refusal]:
    """A published revision is history: a document never changes or removes it."""
    published = {
        address: revision
        for address, revision in walk(resolution.current, FLOW_REVISION_KIND)
        if revision.fields.get("published_at") is not None
    }
    for change in (*resolution.changes, *resolution.deletions):
        if change.kind != FLOW_REVISION_KIND or change.address not in published:
            continue
        if change.action in (ChangeAction.UPDATE, ChangeAction.DELETE):
            field = change.fields[0] if change.fields else "published_at"
            yield Refusal(change.path, ErrorCode.CONFIGURATION_REVISION_PUBLISHED, field)


INTEGRITY: tuple[IntegrityRule, ...] = (
    _pool_rules,
    _flow_rules,
    _default_rules,
    _immutable_rules,
)
"""The rules across objects that concern skills, pools and flows."""
