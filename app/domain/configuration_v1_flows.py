"""A group's legacy pool, flows and their revisions in a configuration document (ADR 0121 2b-ii).

A flow is addressed by its group and ``code``, a revision by its flow and number, and a group's
legacy pool is the one pool of the group, so it has no key. Every reference inside the document
is a natural key: a revision names its counted pool by name (``capacity_pool``) or a group's
legacy pool by the group's slug (``legacy_pool_of``), the skills it requires by code, and its
subjects by subject code. Ids appear nowhere.

Revisions are history. A document carries every revision of a flow with its ``published_at``,
and the flow says which is the current one and which is the draft; ``published_by`` is a staff
id, staff are state, and it is therefore not carried. A published revision is never changed or
removed by a document, a draft may be replaced, and a ``replace`` document never trims the
revisions it leaves out (``Kind.retain_omitted``). A flow still referred to by a booking is
archived, not deleted, when a ``replace`` document leaves it out (``Kind.archive``).

The checks here are those of one object (``FlowRevision`` constructor, the closed table of
interaction kind and handling action, the shape of a flow); the rules across objects
(references, default paths, readiness, the legacy shape, immutability) are in
``app.domain.configuration_v1_flow_rules``.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import cast

from app.domain.authorization import Permission
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.configuration_changes import RuleContext
from app.domain.configuration_checks import invalid, strings_of, text_map, text_of, utc_moment
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
from app.domain.configuration_values import Json, Value
from app.domain.routing import MAX_LABEL_LENGTH, SUBJECT_CODE, validate_labels
from app.domain.scheduling import MAX_SLUG_LENGTH
from app.domain.service_flows import (
    COMBINATIONS,
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
)

MAX_DURATION_MINUTES = 480
"""The longest appointment a flow offers (``valid_slot_minutes``)."""
MIN_DURATION_MINUTES = 5
"""The shortest appointment a flow offers."""
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
    raw = merged.get("subjects")
    if not isinstance(raw, Mapping):
        raise invalid("subject_skill_ids")
    result: dict[str, list[str]] = {}
    for code, skills in raw.items():
        if not SUBJECT_CODE.fullmatch(code) or not isinstance(skills, list):
            raise invalid("subject_skill_ids")
        extra = sorted({skill for skill in skills if isinstance(skill, str)})
        if len(extra) != len(set(map(str, skills))) or any(
            not SUBJECT_CODE.fullmatch(skill) for skill in extra
        ):
            raise invalid("subject_skill_ids")
        result[code] = extra
    return dict(sorted(result.items()))


def _legacy_pool_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a group's legacy pool as ``CapacityPool`` does; return it normalised."""
    CapacityPool(None, 0, text_of(merged, "name"), PoolMode.LEGACY_GROUP, group_id=0)
    return {**merged, "guaranteed_skills": cast("Json", skill_codes(merged, "guaranteed_skills"))}


def _flow_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check what one flow says about itself, as ``ServiceFlow`` does; return it normalised.

    Raises:
        DomainError: ``invalid_configuration`` with ``flow_code``, ``default_for``,
            ``revision``, ``draft_revision`` or ``state``.
    """
    if not SUBJECT_CODE.fullmatch(text_of(merged, "code")):
        raise invalid("flow_code")
    current_number = merged.get("current_revision")
    draft_number = merged.get("draft_revision")
    if current_number is None and draft_number is None:
        raise invalid("revision")
    if current_number is not None and current_number == draft_number:
        raise invalid("draft_revision")
    if merged.get("state") == FlowState.ACTIVE.value and current_number is None:
        raise invalid("state")
    return {**merged, "default_for": cast("Json", modes_of(merged, "default_for"))}


def _revision_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check one revision as ``FlowRevision`` and the closed combination table do.

    A published revision, and any revision with labels, has valid labels in both required
    locales; a draft may still be empty. Returns the revision normalised.

    Raises:
        DomainError: ``invalid_configuration`` with ``revision``, ``labels``, ``intake_modes``,
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
    if kind is not None and action is not None:
        if action not in {a.value for a in COMBINATIONS[InteractionKind(str(kind))].actions}:
            raise invalid("handling_action")
    dispatch, completion = merged.get("dispatch"), merged.get("completion")
    FlowRevision(
        None,
        int(text_of(merged, "revision")),
        labels,
        frozenset(IntakeMode(mode) for mode in modes),
        InteractionKind(str(kind)) if kind is not None else None,
        cast("int | None", merged.get("duration_minutes")),
        None,
        frozenset(range(len(required))),
        {code: frozenset[int]() for code in subjects},
        DispatchKind(str(dispatch)) if dispatch is not None else None,
        HandlingAction(str(action)) if action is not None else None,
        CompletionPolicy(str(completion)) if completion is not None else None,
    )
    return {
        **merged,
        "labels": cast("Json", labels),
        "required_skills": cast("Json", required),
        "subjects": cast("dict[str, Json]", subjects),
        "intake_modes": cast("Json", modes),
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
            minimum=MIN_DURATION_MINUTES,
            maximum=MAX_DURATION_MINUTES,
        ),
        text(
            "capacity_pool",
            nullable=True,
            max_length=MAX_LABEL_LENGTH,
            columns=("capacity_pool_id",),
            description="The name of the counted pool the revision draws on.",
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
