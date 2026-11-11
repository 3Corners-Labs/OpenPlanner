"""Skills and counted capacity pools in a configuration document (ADR 0121 step 2b-ii).

A skill belongs to an organisation and is addressed by its ``code``; a counted pool is addressed
by its ``name`` within the organisation (the table has no code, so the name is the natural key
and a document with two pools of one name is refused when it is read). Both are archived, never
deleted, while anything still refers to them: a skill a person holds or a revision requires, a
pool a revision draws on. Which person holds which skill is *state* and never in a document
(``staff`` and ``staff_memberships`` are state tables), so archiving keeps every grant.

The rules are the ones of the screens: ``Skill`` and ``CapacityPool`` validate code, labels and
name when they are built. The rules across objects (a pool guarantees only the organisation's own
skills, a pool is not archived under a flow that draws on it) are in
``app.domain.configuration_v1_flow_rules``.
"""

from collections.abc import Mapping
from typing import cast

from app.domain.authorization import Permission
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.configuration_changes import RuleContext
from app.domain.configuration_checks import invalid, strings_of, text_map, text_of
from app.domain.configuration_schema import (
    Collection,
    Kind,
    Shape,
    boolean,
    json_object,
    strings,
    text,
)
from app.domain.configuration_v1_names import CAPACITY_POOL_KIND, SKILL_KIND
from app.domain.configuration_values import Json, Value
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
    Skill(None, 0, text_of(merged, "code"), labels)
    return {**merged, "labels": cast("Json", labels)}


def _pool_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a counted pool as ``CapacityPool`` does; return it normalised.

    Raises:
        DomainError: ``invalid_configuration`` with ``pool_name`` or ``guaranteed_skills``.
    """
    CapacityPool(None, 0, text_of(merged, "name"), PoolMode.COUNTED)
    return {**merged, "guaranteed_skills": cast("Json", skill_codes(merged, "guaranteed_skills"))}


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
    ),
    check=_skill_check,
    archive=("archived", True),
    description="A skill of the organisation's catalogue.",
)

CAPACITY_POOL = Kind(
    CAPACITY_POOL_KIND,
    key=("name",),
    permission=Permission.CAPACITY_MANAGE,
    tables=frozenset({"capacity_pools", "capacity_pool_skills"}),
    fields=(
        text("name", required=True, max_length=MAX_LABEL_LENGTH),
        strings(
            "guaranteed_skills",
            max_length=MAX_CODE_LENGTH,
            columns=("capacity_pool_skills.skill_id",),
            description="Codes of the skills every counted unit of the pool holds.",
        ),
        boolean("archived", default=False, columns=("archived_at",)),
    ),
    check=_pool_check,
    archive=("archived", True),
    derived=frozenset({"capacity_pools.mode"}),
    description="A counted capacity pool, addressed by its name within the organisation.",
)

SKILLS = Collection("skills", SKILL, Shape.ENTITIES, "The organisation's skill catalogue.")
CAPACITY_POOLS = Collection(
    "capacity_pools", CAPACITY_POOL, Shape.ENTITIES, "The organisation's counted capacity pools."
)
"""The ``skills`` and ``capacity_pools`` sections of an organisation."""
