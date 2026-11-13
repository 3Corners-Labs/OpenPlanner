"""Eligibility by skill and the requirement of a booking (ADR 0121 step 4, pure rules)."""

import pytest

from app.domain.flow_readiness import (
    ReadinessCheck,
    ReadinessReason,
    readiness,
)
from app.domain.service_flows import (
    requirement_of,
)
from app.domain.skills import Eligible, Missing, eligibility
from tests.support.service_flows import legacy, pool, revision

X, Y, Z = 1, 2, 3


def test_eligibility_is_all_of_and_names_what_is_missing() -> None:
    assert eligibility({X, Y}, {X, Y, Z}) == Eligible()
    assert eligibility({X, Y}, {X}) == Missing(frozenset({Y}))
    assert eligibility({X, Y}, set()) == Missing(frozenset({X, Y}))


def test_nothing_required_is_eligible_for_anyone() -> None:
    assert eligibility(set(), set()) == Eligible()


def test_missing_always_names_a_skill() -> None:
    with pytest.raises(ValueError, match="Missing"):
        Missing(frozenset())


def test_the_requirement_is_the_flow_plus_the_chosen_subject() -> None:
    made_under = revision(
        required_skill_ids=frozenset({X}), subject_skill_ids={"a": frozenset({Y})}
    )

    assert requirement_of(made_under, None) == {X}
    assert requirement_of(made_under, "a") == {X, Y}
    assert requirement_of(made_under, "unbound") == {X}


def test_no_flow_and_a_legacy_flow_require_nothing() -> None:
    assert requirement_of(None, "a") == frozenset()
    assert requirement_of(legacy(), "a") == frozenset()


def test_two_people_with_one_skill_each_are_no_unit_with_both() -> None:
    """A13: a pool that guarantees X and a pool that guarantees Y are no X+Y pool, and the
    flow that requires both cannot be published on either."""
    needs_both = revision(required_skill_ids=frozenset({X, Y}))

    for guaranteed in ({X}, {Y}):
        skills = readiness(needs_both, pool(guaranteed=frozenset(guaranteed)), offered_subjects=())
        item = next(i for i in skills if i.code is ReadinessCheck.SKILLS)
        assert item.reason is ReadinessReason.POOL_DOES_NOT_GUARANTEE

    ready = readiness(needs_both, pool(guaranteed=frozenset({X, Y})), offered_subjects=())
    assert next(i for i in ready if i.code is ReadinessCheck.SKILLS).ok
