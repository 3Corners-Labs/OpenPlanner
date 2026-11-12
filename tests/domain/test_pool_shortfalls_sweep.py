"""The sweep-line shortfalls against the quadratic reference, and its speed (ADR 0122)."""

import random
import time as clock
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.errors import DomainError
from app.domain.pool_accounting import (
    Commitment,
    Shortfall,
    _live,
    one_per_key,
    shortfall_page,
    shortfalls,
)
from app.domain.pool_staffing import Segment, Supply
from app.domain.scheduling import Offer

BASE = datetime(2026, 3, 2, 8, 0, tzinfo=UTC)


def minute(n: int) -> datetime:
    return BASE + timedelta(minutes=n)


def reference(
    supply: Supply,
    commitments: list[Commitment],
    lower: datetime,
    upper: datetime,
    overrun_at: datetime | None = None,
) -> list[Shortfall]:
    """The first, quadratic implementation, kept as the specification."""
    live = _live(commitments, lower, upper, None, overrun_at)
    edges = {lower, upper, *supply.borders_between(lower, upper)}
    for start, until in live:
        edges |= {e for e in (start, until) if e is not None and lower < e < upper}
    ordered = sorted(edges)
    found: list[Shortfall] = []
    for start, end in zip(ordered, ordered[1:], strict=False):
        units = supply.units_at(start)
        load = sum(1 for s, u in live if s <= start and (u is None or start < u))
        if load <= units:
            continue
        previous = found[-1] if found else None
        if previous and previous.end == start and (previous.units, previous.load) == (units, load):
            found[-1] = Shortfall(previous.start, end, units, load)
        else:
            found.append(Shortfall(start, end, units, load))
    return found


def scene(rng: random.Random, count: int, span: int) -> tuple[Supply, list[Commitment]]:
    """Random staffed segments (touching, gapped, 0..3 units) and commitments, some repeated."""
    segments = []
    at = rng.randrange(0, 30)
    while at < span:
        length = rng.randrange(5, 90)
        if rng.random() < 0.9:
            segments.append(
                Segment(
                    minute(at),
                    minute(at + length),
                    rng.randrange(1, 4),
                    rng.choice(list(Offer)),
                )
            )
        at += length + (rng.randrange(0, 30) if rng.random() < 0.5 else 0)
    commitments = []
    for _ in range(count):
        start = rng.randrange(-60, span)
        commitments.append(
            Commitment(
                ("booking", rng.randrange(0, max(1, count))),
                minute(start),
                minute(start + rng.randrange(5, 60)),
                in_progress=rng.random() < 0.3,
            )
        )
    return Supply(tuple(segments)), commitments


@pytest.mark.parametrize("seed", range(300))
def test_the_sweep_gives_what_the_reference_gives_on_random_scenes(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - a test scene, not a secret
    supply, commitments = scene(rng, rng.randrange(0, 40), 600)
    lower = minute(rng.randrange(-30, 200))
    upper = lower + timedelta(minutes=rng.randrange(1, 500))
    overrun_at = rng.choice([None, minute(rng.randrange(0, 600))])

    got = shortfalls(supply, commitments, lower, upper, overrun_at=overrun_at)

    assert got == reference(supply, list(one_per_key(commitments)), lower, upper, overrun_at)
    page, total = shortfall_page(supply, commitments, lower, upper, 3, overrun_at=overrun_at)
    assert page == got[:3]
    assert total == len(got)


def test_a_page_of_zero_still_counts_and_a_bad_interval_is_refused() -> None:
    supply = Supply((Segment(minute(0), minute(60), 1),))
    held = [Commitment(("booking", i), minute(10), minute(20)) for i in range(3)]

    assert shortfall_page(supply, held, minute(0), minute(60), 0) == ([], 1)
    with pytest.raises(DomainError):
        shortfall_page(supply, held, minute(60), minute(0), 5)


def test_thousands_of_commitments_are_swept_fast_and_agree_with_the_reference_on_a_slice() -> None:
    rng = random.Random(7)  # noqa: S311 - a test scene, not a secret
    supply, commitments = scene(rng, 30000, 14 * 24 * 60)
    commitments = [Commitment(("booking", i), c.start, c.until) for i, c in enumerate(commitments)]
    lower, upper = minute(0), minute(14 * 24 * 60)

    began = clock.perf_counter()
    page, total = shortfall_page(supply, commitments, lower, upper, 20)
    took = clock.perf_counter() - began

    assert took < 5
    assert len(page) == 20
    assert total >= 20
    small = [c for c in commitments if c.start < minute(1000)]
    assert shortfalls(supply, small, lower, minute(1000)) == reference(
        supply, small, lower, minute(1000)
    )
