"""Skills and the ALL_OF eligibility rule (ADR 0121 points 5 and 6)."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.skills import (
    Skill,
    check_skill_save,
    check_skills_of,
    check_staff_grant,
    effective_requirements,
    eligible,
    missing_skills,
)
from tests.support.service_flows import skill


def refused(code: str = "passport", labels: dict[str, str] | None = None) -> str | None:
    """Return the field of the error the skill built from the arguments raises."""
    with pytest.raises(DomainError) as caught:
        skill(code=code, labels=labels)
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    return caught.value.field


@pytest.mark.parametrize("code", ["", "Passport", "-x", "a b", "a" * 41])
def test_a_bad_code_is_refused(code: str) -> None:
    assert refused(code=code) == "skill_code"


def test_the_code_grammar_boundaries_are_accepted() -> None:
    assert skill(code="a" * 40).code == "a" * 40
    assert skill(code="0-a").code == "0-a"


@pytest.mark.parametrize(
    "labels",
    [{}, {"nl": "x"}, {"en": "x"}, {"nl": "x", "en": "  "}, {"nl": "", "en": "x"}],
)
def test_both_required_labels_must_be_non_empty(labels: dict[str, str]) -> None:
    assert refused(labels=labels) == "skill_label"


def test_a_label_may_be_80_characters_but_not_81() -> None:
    assert skill(labels={"nl": "a" * 80, "en": "b"}).labels["nl"] == "a" * 80
    assert refused(labels={"nl": "a" * 81, "en": "b"}) == "skill_label"
    assert refused(labels={"nl": "a", "en": "b", "de": "c" * 81}) == "skill_label"


def test_a_label_with_a_control_character_is_refused_in_any_locale() -> None:
    assert refused(labels={"nl": "a\nb", "en": "b"}) == "skill_label"
    assert refused(labels={"nl": "a", "en": "b", "de": "c\x00"}) == "skill_label"
    assert refused(labels={"nl": "a", "en": "b", "de": " "}) == "skill_label"


def test_a_skill_is_archived_exactly_when_it_has_an_archive_time() -> None:
    assert skill(archived_at=datetime(2026, 1, 1, tzinfo=UTC)).archived
    assert not skill().archived


def test_missing_and_eligible() -> None:
    assert missing_skills({1, 2, 3}, {2}) == {1, 3}
    assert missing_skills([], [5]) == frozenset()
    assert eligible({1, 2}, {1, 2, 3})
    assert not eligible({1, 2}, {1})
    assert eligible(set(), set())


def test_effective_requirements_is_the_union() -> None:
    assert effective_requirements({1}, {2}) == {1, 2}
    assert effective_requirements({1}, {1}) == {1}
    assert effective_requirements(set(), set()) == frozenset()


# --- the rules the storage adapters and the fake share ----------------------------------------


def refusal(call: Callable[[], object]) -> tuple[ErrorCode, str | None]:
    """Call ``call`` and return the code and field of the domain error it raises."""
    with pytest.raises(DomainError) as caught:
        call()
    return caught.value.code, caught.value.field


def loaded(skill_id: int, organisation: int = 1, archived: bool = False) -> Skill:
    """Return a stored skill as the adapter loads it."""
    return replace(
        skill(code=f"s{skill_id}"),
        id=skill_id,
        organisation_id=organisation,
        archived_at=datetime(2026, 1, 1, tzinfo=UTC) if archived else None,
    )


def test_a_new_skill_needs_only_a_free_code() -> None:
    check_skill_save(skill(), None, False)
    assert refusal(lambda: check_skill_save(skill(), None, True)) == (
        ErrorCode.SLUG_TAKEN,
        "skill_code",
    )


def test_an_update_needs_the_stored_skill_of_the_same_organisation() -> None:
    update = replace(skill(), id=4)
    assert refusal(lambda: check_skill_save(update, None, False)) == (
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        "skill_id",
    )
    assert refusal(lambda: check_skill_save(update, loaded(4, organisation=2), False)) == (
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        "organisation_id",
    )
    check_skill_save(update, loaded(4), False)
    assert refusal(lambda: check_skill_save(update, loaded(4), True)) == (
        ErrorCode.SLUG_TAKEN,
        "skill_code",
    )


def test_skills_named_by_an_object_are_the_organisations_own_and_not_newly_archived() -> None:
    skills = {1: loaded(1), 2: loaded(2, organisation=2), 3: loaded(3, archived=True)}

    def check(wanted: set[int], already: frozenset[int] = frozenset()) -> None:
        check_skills_of(1, wanted, skills, already, "x")

    check(set())
    check({1})
    check({3}, frozenset({3}))
    assert refusal(lambda: check({1, 9})) == (ErrorCode.CONFIGURATION_OBJECT_MISSING, "x")
    assert refusal(lambda: check({1, 2})) == (ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "x")
    assert refusal(lambda: check({3})) == (ErrorCode.INVALID_CONFIGURATION, "x")
    assert refusal(lambda: check({3}, frozenset({1}))) == (ErrorCode.INVALID_CONFIGURATION, "x")


def test_a_staff_grant_needs_a_membership_to_add_but_not_to_remove() -> None:
    skills = {1: loaded(1), 2: loaded(2), 3: loaded(3, archived=True), 5: loaded(5, organisation=2)}

    def grant(
        wanted: set[int], held: set[int] = frozenset(), member_of: set[int] = frozenset({1})
    ) -> None:
        check_staff_grant(1, member_of, skills, wanted, held)

    grant({1, 2})
    grant({1}, {1, 2})  # removing 2
    grant(set(), {1}, member_of=set())  # a stale grant of a former member can be removed
    grant({1}, {1}, member_of=set())  # keeping what is held needs nothing new
    grant({3}, {3})  # an archived grant stays
    assert refusal(lambda: grant({1}, member_of=set())) == (
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        "organisation_id",
    )
    assert refusal(lambda: grant({3})) == (ErrorCode.INVALID_CONFIGURATION, "skill_ids")
    assert refusal(lambda: grant({5})) == (ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "skill_ids")
    assert refusal(lambda: grant({9})) == (ErrorCode.CONFIGURATION_OBJECT_MISSING, "skill_ids")
