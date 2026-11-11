"""Capacity pools and what they guarantee (ADR 0121 point 6)."""

import pytest

from app.domain.capacity import guarantees, unguaranteed
from app.domain.errors import DomainError, ErrorCode
from tests.support.service_flows import pool


@pytest.mark.parametrize("name", ["", "   ", "x" * 81, "a\nb", "a\x00"])
def test_a_blank_long_or_control_character_name_is_refused(name: str) -> None:
    with pytest.raises(DomainError) as caught:
        pool(name)
    assert (caught.value.code, caught.value.field) == (ErrorCode.INVALID_CONFIGURATION, "pool_name")


def test_surrounding_spaces_do_not_count_towards_the_name_length_and_are_kept() -> None:
    assert pool("x").name == "x"
    assert pool("x" * 80).name == "x" * 80
    assert pool("  " + "x" * 80 + "  ").name == "  " + "x" * 80 + "  "


def test_guarantees_needs_every_required_skill() -> None:
    held = pool(guaranteed=frozenset({1, 2}))
    assert guarantees(held, {1})
    assert guarantees(held, {1, 2})
    assert guarantees(held, set())
    assert not guarantees(held, {1, 3})
    assert unguaranteed(held, {1, 3, 4}) == {3, 4}


def test_a_pool_without_guarantees_guarantees_only_nothing() -> None:
    assert guarantees(pool(), set())
    assert not guarantees(pool(), {1})


def test_the_guarantee_is_not_the_union_of_people_acceptance_a13() -> None:
    # Person A holds X (1), person B holds Y (2); the pool guarantees only X.
    only_x = pool(guaranteed=frozenset({1}))
    assert guarantees(only_x, {1})
    assert not guarantees(only_x, {1, 2})
    assert unguaranteed(only_x, {1, 2}) == {2}
