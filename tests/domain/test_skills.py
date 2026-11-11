"""Skills and the ALL_OF eligibility rule (ADR 0121 points 5 and 6)."""

from datetime import UTC, datetime

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.skills import effective_requirements, eligible, missing_skills
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
