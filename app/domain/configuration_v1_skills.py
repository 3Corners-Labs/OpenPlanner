"""Skills and counted capacity pools in a configuration document (ADR 0121 step 2b-ii).

A skill and a counted pool belong to an organisation and are addressed by their ``code``, which
never changes (migration 0110 gave a pool one, the screen of ADR 0121 step 2c asks for it); the
name of a pool is an ordinary field and a rename is an ordinary change. A counted pool also
carries its time zone and staffing (``configuration_v1_pool_staffing``, ADR 0122 step 3). A
counted pool whose stored code is missing (a row an older image wrote) has no address, so an
export refuses it.
Both are archived, never deleted, while anything still refers to them: a skill a person holds or
any revision requires, a pool any revision draws on. Which person holds which skill is *state*
and never in a document (``staff`` and ``staff_memberships`` are state tables), so archiving
keeps every grant.

The rules are the ones of the screens: ``Skill`` and ``CapacityPool`` validate code, labels and
name when they are built, and ``check_skill_save`` and ``check_pool_save`` run on what the
document leaves behind. The rules across objects (a pool guarantees only the organisation's own
skills, a pool is not archived under a flow that draws on it) are in
``app.domain.configuration_v1_flow_rules``.
"""

from collections.abc import Mapping

from app.domain.authorization import Permission
from app.domain.capacity import MAX_WAITING, CapacityPool, PoolMode
from app.domain.configuration_changes import RuleContext
from app.domain.configuration_checks import (
    invalid,
    json_list,
    json_texts,
    strings_of,
    text_map,
    text_of,
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
from app.domain.configuration_v1_names import CAPACITY_POOL_KIND, SKILL_KIND
from app.domain.configuration_v1_pool_staffing import (
    STAFFING_TABLES,
    check_staffing,
    staffing_fields,
)
from app.domain.configuration_values import Value
from app.domain.routing import MAX_LABEL_LENGTH, SUBJECT_CODE
from app.domain.skills import Skill

MAX_CODE_LENGTH = 40
"""The longest skill code (the grammar of a subject code)."""


def skill_codes(merged: Mapping[str, Value], name: str) -> list[str]:
    """Return the skill codes of a list field, without repeats, sorted.

    Raises:
        DomainError: ``invalid_configuration`` naming ``name`` for a text that is no code.
    """
    codes = sorted(set(strings_of(merged, name)))
    if any(not SUBJECT_CODE.fullmatch(code) for code in codes):
        raise invalid(name)
    return codes


def _skill_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a skill as ``Skill`` does; return it normalised.

    Raises:
        DomainError: ``invalid_configuration`` with ``skill_code`` or ``skill_label``.
    """
    labels = text_map(merged.get("labels"), "skill_label")
    contact = merged.get("contact_center_code")
    Skill(
        None,
        0,
        text_of(merged, "code"),
        labels,
        contact_center_code=None if contact is None else str(contact),
    )
    return {**merged, "labels": json_texts(labels)}


def _pool_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a counted pool as ``CapacityPool`` does; return it normalised.

    Raises:
        DomainError: ``invalid_configuration`` with ``pool_code``, ``pool_name``,
            ``guaranteed_skills``, ``timezone``, ``staffing_rules`` or ``staffing_overrides``.
    """
    CapacityPool(None, 0, text_of(merged, "name"), PoolMode.COUNTED, code=text_of(merged, "code"))
    return {
        **merged,
        "guaranteed_skills": json_list(skill_codes(merged, "guaranteed_skills")),
        **check_staffing(merged),
    }


SKILL = Kind(
    SKILL_KIND,
    key=("code",),
    permission=Permission.SKILLS_MANAGE,
    tables=frozenset({"skills"}),
    fields=(
        text("code", required=True, max_length=MAX_CODE_LENGTH),
        json_object("labels", description="The label per locale; nl and en are required."),
        boolean(
            "archived",
            default=False,
            columns=("archived_at",),
            description="Archived skills stay where they are used but are not newly required.",
        ),
        text(
            "contact_center_code",
            nullable=True,
            max_length=64,
            description=(
                "What the organisation's contact-center flow tests for the skill (ADR 0121 step"
                " 4b); unique within the organisation. A dispatch sends the codes of the skills a"
                " booking requires. Null: not mapped; left out of a change: kept as it is."
            ),
        ),
    ),
    check=_skill_check,
    archive=("archived", True),
    description="A skill of the organisation's catalogue.",
)

CAPACITY_POOL = Kind(
    CAPACITY_POOL_KIND,
    key=("code",),
    permission=Permission.CAPACITY_MANAGE,
    tables=frozenset({"capacity_pools", "capacity_pool_skills"}) | STAFFING_TABLES,
    fields=(
        text("code", required=True, max_length=MAX_CODE_LENGTH),
        text("name", required=True, max_length=MAX_LABEL_LENGTH),
        strings(
            "guaranteed_skills",
            max_length=MAX_CODE_LENGTH,
            columns=("capacity_pool_skills.skill_id",),
            description="Codes of the skills every counted unit of the pool holds.",
        ),
        boolean("archived", default=False, columns=("archived_at",)),
        *staffing_fields(),
        integer(
            "max_waiting",
            nullable=True,
            minimum=1,
            maximum=MAX_WAITING,
            description=(
                "How many people without an appointment may wait for the pool at once (ADR 0121"
                " step 7); null: no limit."
            ),
        ),
    ),
    check=_pool_check,
    archive=("archived", True),
    derived=frozenset({"capacity_pools.mode"}),
    description="A counted capacity pool, addressed by its code within the organisation.",
)

SKILLS = Collection("skills", SKILL, Shape.ENTITIES, "The organisation's skill catalogue.")
CAPACITY_POOLS = Collection(
    "capacity_pools", CAPACITY_POOL, Shape.ENTITIES, "The organisation's counted capacity pools."
)
"""The ``skills`` and ``capacity_pools`` sections of an organisation."""
