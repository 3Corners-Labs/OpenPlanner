"""The rules across the skills, pools and flows of a configuration document (ADR 0121 2b-ii).

A document names everything by natural key, so the rules first read the tree a document would
leave behind into one *run*: the skills, pools, groups and flows of every organisation as the
domain objects of the admin screens (``Skill``, ``CapacityPool``, ``ServiceFlow``,
``FlowRevision``), with ids that are only there for this run. Then they call the very functions
the screens call (``check_skill_save``, ``check_pool_save``, ``check_pool_archive``,
``check_draft_save``, ``check_publishable``, ``check_flow_references``, ``check_default_paths``,
``check_state_change``, ``check_revision_editable``, ``readiness``), and the constructors of the
domain objects run their own invariants, so no rule of the screens is written out a second time
here. The run is built once per resolution and shared by every rule below.

Only what the plan creates or changes is checked, as the portal checks what an admin changes:
what is stored and left alone stays valid.

How a document may bring a revision into being:

* A *draft* is checked as a save in the portal is, against the revision it is based on.
* A revision that the document *publishes* (a new revision with ``published_at``, or a draft that
  gains one) is checked as publishing in the portal is: ``check_flow_references`` against the
  revision before it, ``check_publishable`` for the one that becomes current, a ``published_at``
  that is not in the future (``RuleContext.now``) and later than that of every revision before
  it, and numbers without a gap. The revision before it is the stored one; an installation that
  does not have the flow yet is filled in ascending order, each revision checked against the one
  before it in the document, and the first one (nothing to be based on) may keep a skill or
  subject that was archived or removed since: that is history being restored, and an export
  always imports again.
* A published revision that is stored is never changed or removed (``check_revision_editable``).

Two rules keep the new sections from being a way around the screens. A document activates a flow
only when the readiness checklist passes, so it cannot bring a flow live that the checklist
refuses today (every value outside the ``BUILT_*`` constants of ``service_flows``). The flag
``legacy`` skips those tests, so it is accepted only as the exact wrapper the backfill writes
(``check_legacy_shape``), on a group's own legacy pool, which no other group's flow may use.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import count

from app.domain.capacity import (
    CapacityPool,
    PoolMode,
    check_pool_archive,
    check_pool_save,
    same_name,
)
from app.domain.configuration_changes import (
    Change,
    ChangeAction,
    IntegrityRule,
    Refusal,
    Resolution,
)
from app.domain.configuration_checks import flag_of, int_of, strings_of, text_map, text_of
from app.domain.configuration_tree import Address, Entity, path_of, walk
from app.domain.configuration_v1_flows import revision_of
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
from app.domain.default_flows import check_legacy_shape
from app.domain.errors import DomainError, ErrorCode, missing_object
from app.domain.routing_codec import routing_from_json
from app.domain.service_flows import (
    FlowRevision,
    FlowState,
    IntakeMode,
    ServiceFlow,
    can_activate,
    check_default_paths,
    check_draft_save,
    check_flow_references,
    check_publishable,
    check_revision_editable,
    check_state_change,
    draws_on_pool,
    readiness,
    skills_required_on_pool,
)
from app.domain.skills import Skill, check_skill_save

_ARCHIVED = datetime(1970, 1, 1, tzinfo=UTC)
"""Stands for "archived at some time" in the objects of a run; the time itself never matters."""
_POOLS = (CAPACITY_POOL_KIND, LEGACY_POOL_KIND)
_WRITES = (ChangeAction.CREATE, ChangeAction.UPDATE, ChangeAction.ARCHIVE)


def _refusal(path: str, error: DomainError) -> Refusal:
    """Return the refusal a domain error makes at ``path``."""
    return Refusal(path, error.code, error.field)


def _flow_path(address: Address) -> str:
    """Return the path of a flow in a document."""
    group = path_of(path_of("", "organisations", address[:1]), "groups", address[1:2])
    return path_of(group, "flows", address[2:3])


def _revisions_of(flow: Entity) -> dict[int, Entity]:
    """Return the revisions a flow object holds by number."""
    return {int(revision.key[0]): revision for revision in flow.children.get("revisions", ())}


def _published_at(revision: Entity | None) -> datetime | None:
    """Return when a revision object was published, ``None`` for a draft or no revision."""
    value = None if revision is None else revision.fields.get("published_at")
    try:
        moment = None if value is None else datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def _in_effect(flow: Entity) -> dict[int, Entity]:
    """Return the revisions of a flow that stay: the published ones and the draft.

    An unpublished revision that is not the draft is dropped by the writer when the flow's draft
    moves on, so it is not part of what the document leaves behind.
    """
    draft = int_of(flow.fields, "draft_revision")
    return {
        number: revision
        for number, revision in _revisions_of(flow).items()
        if revision.fields.get("published_at") is not None or number == draft
    }


@dataclass(frozen=True)
class _World:
    """The skills, pools and groups of a tree, by natural key, with ids for this run only.

    Attributes:
        organisations: Id of every organisation, by slug.
        groups: Id of every group, by slug (slugs are unique across the installation).
        group_organisation: The organisation slug of every group.
        skills: Every skill by ``(organisation, code)``.
        pools: Every counted pool by ``(organisation, code)``.
        legacy: Every group's legacy pool, by the group's slug.
        pool_by_id: Every pool, counted or legacy, by its id of this run.
        subjects: The subject codes every group offers, by slug.
    """

    organisations: Mapping[str, int]
    groups: Mapping[str, int]
    group_organisation: Mapping[str, str]
    skills: Mapping[tuple[str, str], Skill]
    pools: Mapping[tuple[str, str], CapacityPool]
    legacy: Mapping[str, CapacityPool]
    pool_by_id: Mapping[int, CapacityPool]
    subjects: Mapping[str, frozenset[str]]

    @property
    def skills_by_id(self) -> dict[int, Skill]:
        """Every skill by its id."""
        return {skill.id: skill for skill in self.skills.values() if skill.id is not None}

    def skill_ids(self, organisation: str, codes: list[str]) -> frozenset[int]:
        """Return the ids of the skills with these codes; a code without a skill is left out."""
        return frozenset(
            skill.id
            for code in codes
            if (skill := self.skills.get((organisation, code))) is not None and skill.id is not None
        )


def _read_world(root: Entity) -> _World:
    """Read the skills, pools and groups of a tree; an object that cannot be built is left out."""
    ids = count(1)
    organisations = {address[0]: next(ids) for address, _ in walk(root, ORGANISATION_KIND)}
    skills: dict[tuple[str, str], Skill] = {}
    for address, entity in walk(root, SKILL_KIND):
        try:
            skills[(address[0], address[1])] = Skill(
                next(ids),
                organisations[address[0]],
                address[1],
                text_map(entity.fields.get("labels"), "skill_label"),
                _ARCHIVED if flag_of(entity.fields, "archived", False) else None,
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
    world = _World(organisations, groups, group_organisation, skills, {}, {}, {}, subjects)
    pools: dict[tuple[str, str], CapacityPool] = {}
    legacy: dict[str, CapacityPool] = {}
    for address, entity in walk(root, CAPACITY_POOL_KIND):
        built = _pool(world, entity, next(ids), address, address[1], None)
        if built is not None:
            pools[(address[0], address[1])] = built
    for address, entity in walk(root, LEGACY_POOL_KIND):
        if address[1] not in groups:
            continue
        built = _pool(world, entity, next(ids), address, None, groups[address[1]])
        if built is not None:
            legacy[address[1]] = built
    by_id = {pool.id: pool for pool in (*pools.values(), *legacy.values()) if pool.id is not None}
    return replace(world, pools=pools, legacy=legacy, pool_by_id=by_id)


def _pool(
    world: _World,
    entity: Entity,
    pool_id: int,
    address: Address,
    code: str | None,
    group_id: int | None,
) -> CapacityPool | None:
    """Build the pool an object describes (counted with ``code``, legacy with ``group_id``)."""
    try:
        return CapacityPool(
            pool_id,
            world.organisations[address[0]],
            text_of(entity.fields, "name"),
            PoolMode.COUNTED if group_id is None else PoolMode.LEGACY_GROUP,
            world.skill_ids(address[0], strings_of(entity.fields, "guaranteed_skills")),
            _ARCHIVED if flag_of(entity.fields, "archived", False) else None,
            group_id,
            code,
        )
    except DomainError:
        return None


class _Run:
    """What the rules share: the world, the flows as domain objects and the writes of the plan."""

    def __init__(self, resolution: Resolution) -> None:
        """Read the resolution once: the world, the flows before and after, the changes."""
        self.resolution = resolution
        self.world = _read_world(resolution.resulting)
        self.flows = dict(walk(resolution.resulting, SERVICE_FLOW_KIND))
        self.before = dict(walk(resolution.current, SERVICE_FLOW_KIND))
        self.pools_before = {kind: dict(walk(resolution.current, kind)) for kind in _POOLS}
        self.pools_after = {kind: dict(walk(resolution.resulting, kind)) for kind in _POOLS}
        base = max((*self.world.organisations.values(), *self.world.groups.values()), default=0)
        self.flow_ids = {address: base + 1 + n for n, address in enumerate(sorted(self.flows))}
        self.archived = {
            change.address
            for change in resolution.changes
            if change.kind == SERVICE_FLOW_KIND and change.action is ChangeAction.ARCHIVE
        }
        self._revisions: dict[int, FlowRevision] = {}
        self._objects: dict[Address, ServiceFlow | DomainError] = {}
        self._stored: dict[Address, ServiceFlow | None] = {}

    def pool_for(self, address: Address, fields: Mapping[str, Value]) -> CapacityPool | None:
        """Return the pool a revision's fields name, ``None`` when it names none.

        A group's legacy pool is named by the group's slug and only by the group's own flows.

        Raises:
            DomainError: ``invalid_configuration`` (``legacy_pool_of``) for the legacy pool of
                another group, ``configuration_object_missing`` for a pool not in the tree.
        """
        code, legacy_of = fields.get("capacity_pool"), fields.get("legacy_pool_of")
        if code is None and legacy_of is None:
            return None
        if legacy_of is not None:
            if legacy_of != address[1]:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "legacy_pool_of")
            found = self.world.legacy.get(address[1])
        else:
            found = self.world.pools.get((address[0], str(code)))
        if found is None:
            raise missing_object("capacity_pool_id")
        return found

    def revision(self, address: Address, number: int, entity: Entity) -> FlowRevision:
        """Return the revision an object describes, with the ids of this run.

        Raises:
            DomainError: ``configuration_object_missing`` for a skill or pool the tree does not
                hold, what ``pool_for`` refuses and what ``FlowRevision`` refuses.
        """
        found = self._revisions.get(id(entity))
        if found is None:
            pool = self.pool_for(address, entity.fields)

            def skill_id(code: str, field: str) -> int:
                skill = self.world.skills.get((address[0], code))
                if skill is None or skill.id is None:
                    raise missing_object(field)
                return skill.id

            found = revision_of(
                entity.fields,
                number,
                flow_id=self.flow_ids[address],
                pool_id=None if pool is None else pool.id,
                skill_id=skill_id,
            )
            self._revisions[id(entity)] = found
        return found

    def _flow_from(self, address: Address, flow: Entity) -> ServiceFlow:
        """Build the flow an object describes from the revisions that stay."""
        held = _in_effect(flow)
        current = int_of(flow.fields, "current_revision")
        draft = int_of(flow.fields, "draft_revision")
        try:
            return ServiceFlow(
                self.flow_ids[address],
                self.world.groups[address[1]],
                address[2],
                FlowState(text_of(flow.fields, "state")),
                frozenset(IntakeMode(mode) for mode in strings_of(flow.fields, "default_for")),
                self.revision(address, current, held[current]) if current in held else None,
                self.revision(address, draft, held[draft]) if draft in held else None,
            )
        except ValueError:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "state") from None

    def flow(self, address: Address) -> ServiceFlow:
        """Return the flow of the tree the document leaves behind.

        Raises:
            DomainError: What building a revision or the ``ServiceFlow`` refuses.
        """
        if address not in self._objects:
            try:
                self._objects[address] = self._flow_from(address, self.flows[address])
            except DomainError as error:
                self._objects[address] = error
        found = self._objects[address]
        if isinstance(found, DomainError):
            raise found
        return found

    def stored(self, address: Address) -> ServiceFlow | None:
        """Return the flow as the installation has it now, ``None`` when it has none."""
        if address not in self._stored:
            entity = self.before.get(address)
            try:
                self._stored[address] = None if entity is None else self._flow_from(address, entity)
            except DomainError:
                self._stored[address] = None
        return self._stored[address]

    def built_flows(self) -> dict[Address, ServiceFlow]:
        """Return every flow of the tree that can be built, by address."""
        found: dict[Address, ServiceFlow] = {}
        for address in sorted(self.flows):
            try:
                found[address] = self.flow(address)
            except DomainError:
                continue
        return found


# --- skills ------------------------------------------------------------------------------


def _skill_rules(run: _Run) -> Iterator[Refusal]:
    """A skill is checked as the screen saves it."""
    for change in run.resolution.changes:
        if change.kind != SKILL_KIND or change.action not in _WRITES:
            continue
        skill = run.world.skills.get((change.address[0], change.address[1]))
        if skill is None:
            continue
        created = change.action is ChangeAction.CREATE
        try:
            check_skill_save(
                replace(skill, id=None) if created else skill, None if created else skill, False
            )
        except DomainError as error:
            yield _refusal(change.path, error)


# --- pools -------------------------------------------------------------------------------


def _pool_rules(run: _Run) -> Iterator[Refusal]:
    """A pool guarantees only its organisation's own skills, and is not archived under a flow."""
    flows = run.built_flows()
    for change in run.resolution.changes:
        if change.kind not in _POOLS or change.action not in _WRITES:
            continue
        pool = (
            run.world.pools.get((change.address[0], change.address[1]))
            if change.kind == CAPACITY_POOL_KIND
            else run.world.legacy.get(change.address[1])
        )
        entity = run.pools_after[change.kind].get(change.address)
        if pool is None or entity is None:
            continue
        codes = strings_of(entity.fields, "guaranteed_skills")
        if any((change.address[0], code) not in run.world.skills for code in codes):
            yield Refusal(
                change.path, ErrorCode.CONFIGURATION_OBJECT_MISSING, "guaranteed_skill_ids"
            )
            continue
        yield from _save_pool(run, change, pool, flows)


def _save_pool(
    run: _Run, change: Change, pool: CapacityPool, flows: Mapping[Address, ServiceFlow]
) -> Iterator[Refusal]:
    """Run ``check_pool_save`` (and for an archive ``check_pool_archive``) on one written pool."""
    created = change.action is ChangeAction.CREATE
    old = run.pools_before[change.kind].get(change.address)
    stored = (
        None
        if created or old is None
        else replace(
            pool,
            name=text_of(old.fields, "name"),
            guaranteed_skill_ids=run.world.skill_ids(
                change.address[0], strings_of(old.fields, "guaranteed_skills")
            ),
            archived_at=_ARCHIVED if flag_of(old.fields, "archived", False) else None,
        )
    )
    group_organisation = (
        None
        if pool.group_id is None
        else run.world.organisations.get(run.world.group_organisation[change.address[1]])
    )
    try:
        check_pool_save(
            replace(pool, id=None) if created else pool,
            stored,
            group_organisation,
            run.world.skills_by_id,
            frozenset() if stored is None else stored.guaranteed_skill_ids,
            same_name_exists=_same_name_elsewhere(run, pool),
            still_required=skills_required_on_pool(flows.values(), pool.id or 0),
        )
        if pool.archived_at is not None and (
            change.action is ChangeAction.ARCHIVE or "archived" in change.fields
        ):
            check_pool_archive(
                [flow.code for flow in flows.values() if draws_on_pool(flow, pool.id or 0)]
            )
    except DomainError as error:
        yield _refusal(change.path, error)


def _same_name_elsewhere(run: _Run, pool: CapacityPool) -> bool:
    """Whether another counted pool of the organisation, not archived, has the pool's name."""
    return pool.mode is PoolMode.COUNTED and any(
        other.id != pool.id
        and other.organisation_id == pool.organisation_id
        and other.archived_at is None
        and same_name(other.name) == same_name(pool.name)
        for other in run.world.pools.values()
    )


# --- flows -------------------------------------------------------------------------------


def _touched(run: _Run) -> dict[Address, set[int]]:
    """Return the flows the plan writes, with the revision numbers it writes of each."""
    touched: dict[Address, set[int]] = {}
    for change in run.resolution.changes:
        if change.kind == SERVICE_FLOW_KIND and change.action in _WRITES:
            touched.setdefault(change.address, set())
        if change.kind == FLOW_REVISION_KIND and change.action in _WRITES[:2]:
            touched.setdefault(change.address[:3], set()).add(int(change.key[0]))
    return touched


def _flow_rules(run: _Run) -> Iterator[Refusal]:
    """Check every flow the plan writes: its revisions, its state and the checklist."""
    for address, written in sorted(_touched(run).items()):
        if address in run.flows:
            yield from _check_flow(run, address, written)


def _check_flow(run: _Run, address: Address, written: set[int]) -> Iterator[Refusal]:
    """Check one flow: the pointers and numbers, the revisions written, its state, readiness.

    In this order, so a refusal is blamed on the object that causes it: the pointers and the
    numbers, then each revision written on its own, then the flow built from them, then what a
    revision written must satisfy within the flow, then the state the flow ends in.
    """
    path = _flow_path(address)
    entity = run.flows[address]
    held = _in_effect(entity)
    for name in ("current_revision", "draft_revision"):
        number = int_of(entity.fields, name)
        if number is not None and number not in held:
            yield Refusal(path, ErrorCode.CONFIGURATION_OBJECT_MISSING, name)
            return
    if sorted(held) != list(range(1, len(held) + 1)):
        yield Refusal(path, ErrorCode.INVALID_CONFIGURATION, "revision")
        return
    stored = _revisions_of(run.before[address]) if address in run.before else {}
    numbers = sorted(
        number
        for number in written & set(_revisions_of(entity))
        if _published_at(stored.get(number)) is None  # a stored published one is immutable
    )
    refused = [
        refusal
        for number in numbers
        for refusal in _build_revision(run, address, number, _revisions_of(entity)[number])
    ]
    yield from refused
    if refused:
        return
    try:
        built = run.flow(address)
    except DomainError as error:
        yield _refusal(path, error)
        return
    refused = [
        refusal
        for number in numbers
        for refusal in _check_revision(run, address, number, _revisions_of(entity)[number], built)
    ]
    yield from refused
    if not refused:
        yield from _check_state(run, address, built)


def _revision_path(address: Address, number: int) -> str:
    """Return the path of a revision in a document."""
    return path_of(_flow_path(address), "revisions", (str(number),))


def _build_revision(run: _Run, address: Address, number: int, entity: Entity) -> Iterator[Refusal]:
    """Build the revision a document writes on its own; refuse what it cannot be built from.

    An unpublished revision that is not the flow's draft is refused here too: the writer would
    leave it behind when the draft moves on.
    """
    path = _revision_path(address, number)
    if _published_at(entity) is None and number != int_of(
        run.flows[address].fields, "draft_revision"
    ):
        yield Refusal(path, ErrorCode.INVALID_CONFIGURATION, "published_at")
        return
    try:
        run.revision(address, number, entity)
    except DomainError as error:
        yield _refusal(path, error)


def _check_revision(
    run: _Run, address: Address, number: int, entity: Entity, flow: ServiceFlow
) -> Iterator[Refusal]:
    """Check one revision the plan writes: a draft as a save, a published one as a publication."""
    try:
        built = run.revision(address, number, entity)
        if built.legacy:
            check_legacy_shape(
                address[2], built, run.pool_for(address, entity.fields), flow.group_id
            )
        if _published_at(entity) is not None:
            _check_publication(run, address, number, built)
        else:
            _check_draft(run, address, built, flow)
    except DomainError as error:
        yield _refusal(_revision_path(address, number), error)


def _based_on(run: _Run, address: Address, number: int) -> FlowRevision | None:
    """Return the published revision the one numbered ``number`` follows, ``None`` for the first."""
    earlier = [
        (found, revision)
        for found, revision in _in_effect(run.flows[address]).items()
        if found < number and _published_at(revision) is not None
    ]
    if not earlier:
        return None
    latest, revision = max(earlier, key=lambda item: item[0])
    return run.revision(address, latest, revision)


def _check_references(
    run: _Run,
    address: Address,
    built: FlowRevision,
    based_on: FlowRevision | None,
    *,
    restoring: bool = False,
) -> None:
    """Run ``check_flow_references`` for a revision against the world of this run.

    ``restoring`` is the first revision of a flow the installation does not have: nothing to be
    based on, so what it names stays as it is (history being restored).
    """
    pool = run.world.pool_by_id.get(built.capacity_pool_id or 0)
    offered = run.world.subjects.get(address[1], frozenset())
    kept = based_on if based_on is not None else (built if restoring else None)
    check_flow_references(
        run.world.organisations[address[0]],
        built,
        pool,
        run.world.skills_by_id,
        kept.all_skill_ids if kept else frozenset(),
        offered | frozenset(kept.subject_skill_ids if kept else ()),
        group_id=run.world.groups[address[1]],
        already_pool_id=kept.capacity_pool_id if kept else None,
    )


def _check_draft(run: _Run, address: Address, built: FlowRevision, flow: ServiceFlow) -> None:
    """Check a draft the document writes as the portal checks saving one.

    Raises:
        DomainError: What ``check_draft_save`` and ``check_flow_references`` refuse.
    """
    stored = run.stored(address)
    stored_draft = None if stored is None else stored.draft
    if flow.current is None:
        check_draft_save(replace(flow, id=None), None, False)
    else:
        check_draft_save(flow, replace(flow, draft=stored_draft), False)
    kept = stored_draft or (stored.current if stored else None) or flow.current
    _check_references(run, address, built, kept)


def _check_publication(run: _Run, address: Address, number: int, built: FlowRevision) -> None:
    """Check a revision this document publishes as the portal checks publishing a draft.

    Raises:
        DomainError: ``invalid_configuration`` (``published_at``) for a time in the future or not
            later than an earlier revision's, and what ``check_publishable`` and
            ``check_flow_references`` refuse.
    """
    moment = built.published_at
    flow = run.flows[address]
    earlier = [
        _published_at(revision)
        for found, revision in _in_effect(flow).items()
        if found < number and _published_at(revision) is not None
    ]
    if (
        moment is None
        or moment > run.resolution.context.now
        or any(other is not None and moment <= other for other in earlier)
    ):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "published_at")
    if number == int_of(flow.fields, "current_revision"):
        check_publishable(
            replace(
                run.flow(address),
                state=FlowState.DRAFT,
                current=None,
                draft=replace(built, published_at=None),
            )
        )
    _check_references(
        run, address, built, _based_on(run, address, number), restoring=address not in run.before
    )


def _check_state(run: _Run, address: Address, built: ServiceFlow) -> Iterator[Refusal]:
    """Check the state a flow ends in: the lifecycle, an archive, and readiness when active."""
    path = _flow_path(address)
    old = run.before.get(address)
    if address in run.archived:
        if built.current is None:
            yield Refusal(path, ErrorCode.CONFIGURATION_DRAFT_FLOW_IN_USE, "draft_revision")
        return
    if old is not None and old.fields.get("state") != built.state.value:
        try:
            check_state_change(
                replace(built, state=FlowState(text_of(old.fields, "state"))), built.state
            )
        except DomainError as error:
            yield _refusal(path, error)
    if built.state is FlowState.ACTIVE and built.current is not None:
        pool = run.world.pool_by_id.get(built.current.capacity_pool_id or 0)
        items = readiness(
            built.current, pool, offered_subjects=run.world.subjects.get(address[1], ())
        )
        if not can_activate(items):
            for item in items:
                if not item.ok:
                    yield Refusal(
                        path, ErrorCode.INVALID_CONFIGURATION, f"readiness_{item.code.value}"
                    )


# --- defaults ----------------------------------------------------------------------------


def _default_rules(run: _Run) -> Iterator[Refusal]:
    """No two active flows of a group are the default of the same entry path."""
    groups = {
        c.address[:2]
        for c in run.resolution.changes
        if c.kind in (SERVICE_FLOW_KIND, FLOW_REVISION_KIND)
    }
    active = {
        address: flow
        for address, flow in run.built_flows().items()
        if address[:2] in groups and flow.state is FlowState.ACTIVE
    }
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


# --- history -----------------------------------------------------------------------------


def _immutable_rules(run: _Run) -> Iterator[Refusal]:
    """A published revision is history: a document never changes or removes it."""
    published = {
        address: revision
        for address, revision in walk(run.resolution.current, FLOW_REVISION_KIND)
        if _published_at(revision) is not None
    }
    for change in (*run.resolution.changes, *run.resolution.deletions):
        if change.kind != FLOW_REVISION_KIND or change.address not in published:
            continue
        if change.action in (ChangeAction.UPDATE, ChangeAction.DELETE):
            try:
                check_revision_editable(
                    _published_at(published[change.address]),
                    change.fields[0] if change.fields else "published_at",
                )
            except DomainError as error:
                yield _refusal(change.path, error)


def _rules(resolution: Resolution) -> Iterator[Refusal]:
    """Run every rule on one run of the resolution (the world is read once)."""
    run = _Run(resolution)
    yield from _skill_rules(run)
    yield from _pool_rules(run)
    yield from _flow_rules(run)
    yield from _default_rules(run)
    yield from _immutable_rules(run)


INTEGRITY: tuple[IntegrityRule, ...] = (_rules,)
"""The rules across objects that concern skills, pools and flows."""
