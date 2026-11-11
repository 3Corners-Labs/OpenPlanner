"""Capacity pools and what they guarantee (ADR 0121 point 6)."""

from datetime import UTC, datetime

import pytest

from app.domain.capacity import CapacityPool, PoolMode, guarantees, shareable, unguaranteed
from app.domain.errors import DomainError, ErrorCode


def pool(name: str = "Loket", mode: PoolMode = PoolMode.COUNTED, **kw: object) -> CapacityPool:
    """Return a pool of organisation 1."""
    return CapacityPool(None, 1, name, mode, **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
def test_a_bad_name_is_refused(name: str) -> None:
    with pytest.raises(DomainError) as caught:
        pool(name)
    assert (caught.value.code, caught.value.field) == (ErrorCode.INVALID_CONFIGURATION, "pool_name")


def test_the_name_boundaries_are_accepted() -> None:
    assert pool("x").name == "x"
    assert pool("x" * 80).name == "x" * 80
    assert pool("  x  ").name == "  x  "


def test_guarantees_needs_every_required_skill() -> None:
    held = pool(guaranteed_skill_ids=frozenset({1, 2}))
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
    only_x = pool(guaranteed_skill_ids=frozenset({1}))
    assert guarantees(only_x, {1})
    assert not guarantees(only_x, {1, 2})
    assert unguaranteed(only_x, {1, 2}) == {2}


def test_only_a_counted_pool_is_shareable() -> None:
    assert shareable(pool(mode=PoolMode.COUNTED))
    assert not shareable(pool(mode=PoolMode.LEGACY_GROUP))


def test_archived_at_is_kept() -> None:
    when = datetime(2026, 1, 1, tzinfo=UTC)
    assert pool(archived_at=when).archived_at == when
