"""A group's legacy pool, flows and their revisions in a configuration document (ADR 0121 2b-ii).

A flow is addressed by its group and ``code``, a revision by its flow and number, and a group's
legacy pool is the one pool of the group, so it has no key. Every reference inside the document
is a natural key: a revision names its counted pool by code (``capacity_pool``) or a group's
legacy pool by the group's slug (``legacy_pool_of``), the skills it requires by code, and its
subjects by subject code. Ids appear nowhere.

Revisions are history. A document carries every revision of a flow with its ``published_at``,
and the flow says which is the current one and which is the draft; ``published_by`` is a staff
id, staff are state, and it is therefore not carried. A published revision is never changed or
removed by a document, a draft may be replaced, and a ``replace`` document never trims the
revisions it leaves out (``Kind.retain_omitted``). A flow that is or was active, or that a
booking refers to, is archived, not deleted, when a ``replace`` document leaves it out or a
document deletes it (``Kind.archive``); a flow in state draft was never active and is deleted
with its revisions unless a booking refers to it (then the document is refused).

The checks here are those of one object (the closed table of interaction kind and handling
action, the labels, the normal form of lists); ``revision_of`` is the one mapping of a document
to a ``FlowRevision``. The rules across objects (references, default paths, readiness, the
legacy shape, immutability, publishing) are in ``app.domain.configuration_v1_flow_rules``, which
builds ``ServiceFlow`` and ``FlowRevision`` objects so their own invariants run.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from enum import StrEnum

from app.domain.authorization import Permission
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.configuration_changes import RuleContext
from app.domain.configuration_checks import (
    flag_of,
    int_of,
    invalid,
    json_list,
    json_texts,
    object_of,
    strings_of,
    text_map,
    text_of,
    utc_moment,
)
from app.domain.configuration_schema import (
    Collection,
    Kind,
    Shape,
    boolean,
    integer,
    json_object,
    strings,
    text,
)
from app.domain.configuration_v1_names import (
    FLOW_REVISION_KIND,
    LEGACY_POOL_KIND,
    SERVICE_FLOW_KIND,
)
from app.domain.configuration_v1_skills import MAX_CODE_LENGTH, skill_codes
from app.domain.configuration_values import Value
from app.domain.routing import MAX_LABEL_LENGTH, SUBJECT_CODE, validate_labels
from app.domain.scheduling import (
    MAX_SLOT_MINUTES,
    MAX_SLUG_LENGTH,
    MIN_SLOT_MINUTES,
    valid_slot_minutes,
)
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    check_combination,
)

MAX_REVISION = 9999
"""The highest revision number a document may name."""


def _values(enum: type[StrEnum]) -> tuple[str, ...]:
    """Return the values of an enum, for a closed set of choices."""
    return tuple(member.value for member in enum)


def modes_of(merged: Mapping[str, Value], name: str) -> list[str]:
    """Return the entry paths of a list field, without repeats, sorted.

    Raises:
        DomainError: ``invalid_configuration`` naming ``name`` for an unknown entry path.
    """
    modes = sorted(set(strings_of(merged, name)))
    if any(mode not in _values(IntakeMode) for mode in modes):
        raise invalid(name)
    return modes


def subjects_of(merged: Mapping[str, Value]) -> dict[str, list[str]]:
    """Return a revision's subjects (code to skill codes) normalised: sorted, without repeats.

    Raises:
        DomainError: ``invalid_configuration`` with ``subject_skill_ids`` for a shape that is
            not an object of lists of codes, or a subject code that is none.
    """
    result: dict[str, list[str]] = {}
    for code, skills in object_of(merged, "subjects").items():
        if not SUBJECT_CODE.fullmatch(code) or not isinstance(skills, list):
            raise invalid("subject_skill_ids")
        names = [skill for skill in skills if isinstance(skill, str)]
        if len(names) != len(skills) or len(set(names)) != len(names):
            raise invalid("subject_skill_ids")
        if any(not SUBJECT_CODE.fullmatch(skill) for skill in names):
            raise invalid("subject_skill_ids")
        result[code] = sorted(names)
    if not isinstance(merged.get("subjects"), dict):
        raise invalid("subject_skill_ids")
    return dict(sorted(result.items()))


def revision_of(
    fields: Mapping[str, Value],
    number: int,
    *,
    flow_id: int | None,
    pool_id: int | None,
    skill_id: Callable[[str, str], int],
) -> FlowRevision:
    """Build the revision an object's fields describe; the one mapping of a document to a revision.

    The rules of a document call it with the ids of one run, the writer with the ids of the
    database, so both see the same revision, and ``FlowRevision`` runs its own invariants on it.

    Args:
        fields: The revision's fields as a document or the installation holds them.
        number: The revision number.
        flow_id: The id of its flow.
        pool_id: The id of the pool it names, resolved by the caller (``None`` for no pool).
        skill_id: Gives the id of the skill with a code, from the code and the field to blame; it
            raises when there is no such skill.

    Raises:
        DomainError: What ``skill_id`` raises, and what ``FlowRevision`` refuses.
    """
    kind, action = fields.get("interaction_kind"), fields.get("handling_action")
    dispatch, completion = fields.get("dispatch"), fields.get("completion")
    published = fields.get("published_at")
    return FlowRevision(
        flow_id=flow_id,
        revision=number,
        labels=text_map(fields.get("labels"), "labels"),
        intake_modes=frozenset(IntakeMode(mode) for mode in strings_of(fields, "intake_modes")),
        interaction_kind=InteractionKind(str(kind)) if kind is not None else None,
        duration_minutes=int_of(fields, "duration_minutes"),
        capacity_pool_id=pool_id,
        required_skill_ids=frozenset(
            skill_id(code, "required_skill_ids") for code in strings_of(fields, "required_skills")
        ),
        subject_skill_ids={
            code: frozenset(skill_id(item, "subject_skill_ids") for item in skills)
            for code, skills in subjects_of(fields).items()
        },
        dispatch=DispatchKind(str(dispatch)) if dispatch is not None else None,
        handling_action=HandlingAction(str(action)) if action is not None else None,
        completion=CompletionPolicy(str(completion)) if completion is not None else None,
        collects_email=flag_of(fields, "collects_email", True),
        legacy=flag_of(fields, "legacy", False),
        published_at=datetime.fromisoformat(str(published)) if published is not None else None,
    )


def _duration(value: Value, context: RuleContext) -> Value:
    """Check an appointment length with the one rule of the schedule (``valid_slot_minutes``)."""
    if not isinstance(value, int) or isinstance(value, bool) or not valid_slot_minutes(value):
        raise invalid("duration_minutes")
    return value


def _legacy_pool_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a group's legacy pool as ``CapacityPool`` does; return it normalised."""
    CapacityPool(None, 0, text_of(merged, "name"), PoolMode.LEGACY_GROUP, group_id=0)
    return {**merged, "guaranteed_skills": json_list(skill_codes(merged, "guaranteed_skills"))}


def _flow_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Normalise a flow's entry paths; ``ServiceFlow`` checks the rest when the rules build it.

    Raises:
        DomainError: ``invalid_configuration`` with ``default_for`` for an unknown entry path.
    """
    return {**merged, "default_for": json_list(modes_of(merged, "default_for"))}


def _revision_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Normalise one revision and check what is about this object alone.

    A published revision, and any revision with labels, has valid labels in both required
    locales; a draft may still be empty. The pool is named by code or by service, never both,
    and the action fits the closed table of its interaction kind. Everything else a revision must
    satisfy is checked by ``FlowRevision`` and the rules across objects when they build it.

    Returns:
        The revision normalised.

    Raises:
        DomainError: ``invalid_configuration`` with ``labels``, ``intake_modes``,
            ``subject_skill_ids``, ``required_skill_ids``, ``capacity_pool_id``,
            ``handling_action`` or ``published_at``.
    """
    labels = text_map(merged.get("labels"), "labels")
    published = merged.get("published_at")
    if labels or published is not None:
        validate_labels(labels, "labels")
    required = skill_codes(merged, "required_skills")
    subjects = subjects_of(merged)
    if merged.get("capacity_pool") is not None and merged.get("legacy_pool_of") is not None:
        raise invalid("capacity_pool_id")
    modes = modes_of(merged, "intake_modes")
    kind, action = merged.get("interaction_kind"), merged.get("handling_action")
    check_combination(
        InteractionKind(str(kind)) if kind is not None else None,
        HandlingAction(str(action)) if action is not None else None,
    )
    return {
        **merged,
        "labels": json_texts(labels),
        "required_skills": json_list(required),
        "subjects": {code: json_list(skills) for code, skills in subjects.items()},
        "intake_modes": json_list(modes),
        "published_at": None if published is None else utc_moment(published, "published_at"),
    }


LEGACY_POOL = Kind(
    LEGACY_POOL_KIND,
    permission=Permission.CAPACITY_MANAGE,
    tables=frozenset({"capacity_pools", "capacity_pool_skills"}),
    fields=(
        text("name", required=True, max_length=MAX_LABEL_LENGTH),
        strings(
            "guaranteed_skills",
            max_length=MAX_CODE_LENGTH,
            columns=("capacity_pool_skills.skill_id",),
        ),
        boolean("archived", default=False, columns=("archived_at",)),
    ),
    check=_legacy_pool_check,
    deletable=False,
    derived=frozenset({"capacity_pools.mode"}),
    description="The pool that wraps this group's own staffing; a group has at most one.",
)

FLOW_REVISION = Kind(
    FLOW_REVISION_KIND,
    key=("revision",),
    tables=frozenset(
        {"flow_revisions", "flow_revision_skills", "subject_flows", "subject_flow_skills"}
    ),
    fields=(
        integer("revision", required=True, minimum=1, maximum=MAX_REVISION),
        json_object("labels", default={}, description="The label per locale."),
        strings("intake_modes", max_length=16),
        text("interaction_kind", nullable=True, choices=_values(InteractionKind)),
        integer(
            "duration_minutes",
            nullable=True,
            minimum=MIN_SLOT_MINUTES,
            maximum=MAX_SLOT_MINUTES,
            check=_duration,
        ),
        text(
            "capacity_pool",
            nullable=True,
            max_length=MAX_CODE_LENGTH,
            columns=("capacity_pool_id",),
            description="The code of the counted pool the revision draws on.",
        ),
        text(
            "legacy_pool_of",
            nullable=True,
            max_length=MAX_SLUG_LENGTH,
            columns=("capacity_pool_id",),
            description="The slug of the group whose legacy pool the revision draws on.",
        ),
        strings(
            "required_skills",
            max_length=MAX_CODE_LENGTH,
            columns=("flow_revision_skills.skill_id",),
        ),
        json_object(
            "subjects",
            default={},
            columns=(
                "subject_flows.subject_code",
                "subject_flow_skills.subject_code",
                "subject_flow_skills.skill_id",
            ),
            description="Subject code to the extra skill codes that subject requires.",
        ),
        text("dispatch", nullable=True, choices=_values(DispatchKind)),
        text("handling_action", nullable=True, choices=_values(HandlingAction)),
        text("completion", nullable=True, choices=_values(CompletionPolicy)),
        boolean("collects_email", default=True),
        boolean("legacy", default=False),
        text(
            "published_at",
            nullable=True,
            description="When it was published (UTC); none for a draft.",
        ),
    ),
    check=_revision_check,
    retain_omitted=True,
    derived=frozenset({"flow_revisions.location_id", "flow_revisions.published_by"}),
    description="One revision of a flow; a published one is history and never changes.",
)

SERVICE_FLOW = Kind(
    SERVICE_FLOW_KIND,
    key=("code",),
    permission=Permission.GROUP_EDIT,
    tables=frozenset({"service_flows"}),
    fields=(
        text("code", required=True, max_length=MAX_CODE_LENGTH),
        text("state", required=True, choices=_values(FlowState)),
        strings("default_for", max_length=16, columns=("default_appointment", "default_on_demand")),
        integer("current_revision", nullable=True, minimum=1, maximum=MAX_REVISION),
        integer("draft_revision", nullable=True, minimum=1, maximum=MAX_REVISION),
    ),
    collections=(Collection("revisions", FLOW_REVISION, Shape.ENTITIES, "Every revision."),),
    check=_flow_check,
    archive=("state", FlowState.ARCHIVED.value),
    description="A way a service is taken in and handled, with its revisions.",
)

GROUP_FLOWS = Collection("flows", SERVICE_FLOW, Shape.ENTITIES, "The service's flows.")
GROUP_LEGACY_POOL = Collection(
    "legacy_pool", LEGACY_POOL, Shape.SINGLE, "The pool that wraps the service's own staffing."
)
"""The ``flows`` and ``legacy_pool`` sections of a group."""
