"""Capacity pools and what they guarantee (ADR 0121 point 6)."""

from collections.abc import Callable
from dataclasses import replace

import pytest

from app.domain.capacity import (
    CapacityPool,
    PoolMode,
    check_pool_archive,
    check_pool_save,
    guarantees,
    unguaranteed,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.skills import Skill
from tests.support.domain import NOW
from tests.support.service_flows import pool, skill


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


# --- the rules the storage adapters and the fake share ----------------------------------------


def refusal(call: Callable[[], object]) -> tuple[ErrorCode, str | None]:
    """Call ``call`` and return the code and field of the domain error it raises."""
    with pytest.raises(DomainError) as caught:
        call()
    return caught.value.code, caught.value.field


def loaded_skill(skill_id: int, organisation: int = 1, archived: bool = False) -> Skill:
    """Return a stored skill as the adapter loads it."""
    return replace(
        skill(code=f"s{skill_id}"),
        id=skill_id,
        organisation_id=organisation,
        archived_at=NOW if archived else None,
    )


def legacy_pool(group_id: int | None = 7) -> CapacityPool:
    """Return a new legacy group pool of organisation 1 for ``group_id``."""
    return replace(pool(mode=PoolMode.LEGACY_GROUP), id=None, group_id=group_id)


def test_a_counted_pool_without_a_group_passes() -> None:
    check_pool_save(replace(pool(), id=None), None, None, {}, frozenset())
    check_pool_save(pool(), pool(), None, {}, frozenset())


def test_a_legacy_pool_names_exactly_one_free_group_of_its_organisation() -> None:
    check_pool_save(legacy_pool(), None, 1, {}, frozenset())
    assert refusal(lambda: check_pool_save(legacy_pool(None), None, None, {}, frozenset())) == (
        ErrorCode.INVALID_CONFIGURATION,
        "group_id",
    )
    counted_with_group = replace(pool(), id=None, group_id=7)
    assert refusal(lambda: check_pool_save(counted_with_group, None, 1, {}, frozenset())) == (
        ErrorCode.INVALID_CONFIGURATION,
        "group_id",
    )
    assert refusal(lambda: check_pool_save(legacy_pool(), None, None, {}, frozenset())) == (
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        "group_id",
    )
    assert refusal(lambda: check_pool_save(legacy_pool(), None, 2, {}, frozenset())) == (
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        "group_id",
    )
    assert refusal(
        lambda: check_pool_save(legacy_pool(), None, 1, {}, frozenset(), group_taken=True)
    ) == (ErrorCode.CONFIGURATION_OBJECT_EXISTS, "group_id")


def test_an_update_needs_the_stored_pool_and_never_changes_mode_or_group() -> None:
    assert refusal(lambda: check_pool_save(pool(), None, None, {}, frozenset())) == (
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        "pool_id",
    )
    other_organisation = replace(pool(), organisation_id=2)
    assert refusal(lambda: check_pool_save(pool(), other_organisation, None, {}, frozenset())) == (
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        "organisation_id",
    )
    was_legacy = replace(pool(mode=PoolMode.LEGACY_GROUP), group_id=7)
    assert refusal(lambda: check_pool_save(pool(), was_legacy, None, {}, frozenset())) == (
        ErrorCode.INVALID_CONFIGURATION,
        "mode",
    )
    moved = replace(was_legacy, group_id=8)
    assert refusal(lambda: check_pool_save(moved, was_legacy, 1, {}, frozenset())) == (
        ErrorCode.INVALID_CONFIGURATION,
        "mode",
    )
    check_pool_save(was_legacy, was_legacy, 1, {}, frozenset())


def test_a_pool_guarantees_own_skills_and_an_archived_one_only_if_it_already_does() -> None:
    skills = {
        1: loaded_skill(1),
        2: loaded_skill(2, organisation=2),
        3: loaded_skill(3, archived=True),
    }

    def save(guaranteed: set[int], already: frozenset[int] = frozenset()) -> None:
        wanted = replace(pool(), id=None, guaranteed_skill_ids=frozenset(guaranteed))
        check_pool_save(wanted, None, None, skills, already)

    save({1})
    save({3}, frozenset({3}))
    assert refusal(lambda: save({1, 9})) == (
        ErrorCode.CONFIGURATION_OBJECT_MISSING,
        "guaranteed_skill_ids",
    )
    assert refusal(lambda: save({2})) == (
        ErrorCode.CONFIGURATION_OUT_OF_SCOPE,
        "guaranteed_skill_ids",
    )
    assert refusal(lambda: save({3})) == (ErrorCode.INVALID_CONFIGURATION, "guaranteed_skill_ids")


def test_a_pool_with_an_active_flow_cannot_be_archived() -> None:
    check_pool_archive([])
    assert refusal(lambda: check_pool_archive(["a"])) == (
        ErrorCode.CONFIGURATION_OBJECT_IN_USE,
        "capacity_pool",
    )
