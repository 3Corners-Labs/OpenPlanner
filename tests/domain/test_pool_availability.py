"""Free starts of a pool flow from one scene (ADR 0122 step 5, review finding 2).

``PoolScene.offered`` admits each start against only the commitments and segments that overlap
it. ``reference`` below is the straightforward computation the first version used (every start
against every commitment and segment); random scenes must give the same answer, and a busy month
must stay far below a second.
"""

import random
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.availability import AvailableSlot
from app.domain.booking import Slot
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import ResolvedFlow
from app.domain.pool_accounting import Commitment, admit_appointment, interval_end
from app.domain.pool_availability import PoolScene, PoolTarget, resolved_target, slot_target
from app.domain.pool_booking import SceneTimes
from app.domain.pool_staffing import Intake, Segment, Supply, lowest_units
from app.domain.scheduling import Offer
from tests.support.domain import MONDAY, make_group

DAY0 = datetime(2026, 3, 2, tzinfo=UTC)


def reference(
    starts: list[datetime],
    supply: Supply,
    commitments: list[Commitment],
    target: PoolTarget,
    buffer: int,
) -> list[AvailableSlot]:
    """The first version: every start against the whole supply and every commitment."""
    length = timedelta(minutes=target.duration_minutes)
    found = []
    for start in starts:
        admission = admit_appointment(supply, commitments, start, target.duration_minutes, buffer)
        if admission.admitted:
            until = interval_end(start, target.duration_minutes, buffer)
            units = lowest_units(supply, start, until, Intake.APPOINTMENT)
            found.append(AvailableSlot(start, start + length, units, admission.free))
    return found


def _scene(rng: random.Random, days: int) -> tuple[list[datetime], Supply, list[Commitment]]:
    """A random pool: a few blocks a day, five-minute starts, commitments of 5 to 600 minutes."""
    segments = []
    for day in range(days):
        moment = DAY0 + timedelta(days=day, minutes=rng.randrange(0, 120, 5))
        for _ in range(rng.randint(1, 3)):
            length = timedelta(minutes=rng.randrange(30, 240, 5))
            offer = rng.choice([Offer.SCHEDULED, Offer.BOTH, Offer.DIRECT])
            segments.append(Segment(moment, moment + length, rng.randint(1, 4), offer))
            moment += length + timedelta(minutes=rng.choice([0, 0, 15, 60]))
    end = DAY0 + timedelta(days=days)
    starts = [DAY0 + timedelta(minutes=5 * n) for n in range(days * 288)]
    commitments = []
    for n in range(days * 30):
        start = DAY0 + timedelta(minutes=5 * rng.randrange(days * 288))
        span = timedelta(minutes=rng.randrange(5, 600, 5))
        commitments.append(Commitment(("booking", n), start, min(start + span, end)))
    return starts, Supply(tuple(segments)), commitments


@pytest.mark.parametrize("seed", range(12))
def test_the_indexed_scene_agrees_with_the_straightforward_computation(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - test data, not security
    starts, supply, commitments = _scene(rng, days=2)
    target = PoolTarget(1, rng.choice([5, 15, 20, 45, 90]))
    buffer = rng.choice([0, 5, 10])

    scene = PoolScene(target, buffer, starts, supply, commitments)

    expected = reference(starts, supply, commitments, target, buffer)
    assert scene.offered(DAY0, DAY0 + timedelta(days=2)) == expected
    middle = DAY0 + timedelta(hours=30)
    assert scene.offered(middle, DAY0 + timedelta(days=2)) == [
        s for s in expected if s.start >= middle
    ]


def test_a_busy_month_of_five_minute_starts_stays_far_below_a_second() -> None:
    days = 31
    segments = tuple(
        Segment(DAY0 + timedelta(days=d), DAY0 + timedelta(days=d, hours=23, minutes=55), 1000)
        for d in range(days)
    )
    rng = random.Random(7)  # noqa: S311 - test data, not security
    commitments = [
        Commitment(("booking", n), at, at + timedelta(minutes=rng.randrange(5, 60, 5)))
        for n in range(3000)
        for at in [DAY0 + timedelta(minutes=5 * rng.randrange(days * 280))]
    ]
    starts = [DAY0 + timedelta(minutes=5 * n) for n in range(days * 288)]
    scene = PoolScene(PoolTarget(1, 5), 0, starts, Supply(segments), commitments)

    began = time.perf_counter()
    found = scene.offered(DAY0, DAY0 + timedelta(days=days))
    elapsed = time.perf_counter() - began

    assert len(starts) == 8928
    assert len(found) == days * 287  # every start whose five minutes fit before 23:55
    assert elapsed < 1.0, elapsed


def test_an_empty_scene_offers_nothing() -> None:
    scene = PoolScene(PoolTarget(1, 20), 0, [], Supply(), [])

    assert scene.offered(DAY0, DAY0 + timedelta(days=1)) == []


def test_resolved_target_of_a_flow() -> None:
    def resolved(**changes: object) -> ResolvedFlow:
        fields: dict[str, object] = {
            "flow_id": 1,
            "code": "advies",
            "revision": 1,
            "interaction_kind": None,
            "legacy": False,
            "capacity_pool_id": 4,
            "duration_minutes": 20,
        }
        fields.update(changes)
        return ResolvedFlow(**fields)  # type: ignore[arg-type]

    assert resolved_target(None) is None
    assert resolved_target(resolved(legacy=True)) is None
    assert resolved_target(resolved()) == PoolTarget(4, 20)
    for broken in (resolved(capacity_pool_id=None), resolved(duration_minutes=None)):
        with pytest.raises(DomainError) as error:
            resolved_target(broken)
        assert error.value.code is ErrorCode.SLOT_UNAVAILABLE


def test_the_slot_of_a_booking_says_where_it_sits() -> None:
    legacy = Slot(1, 1, DAY0, DAY0 + timedelta(minutes=30), 2)
    pool = Slot(2, 1, DAY0, DAY0 + timedelta(minutes=20), 2, capacity_pool_id=9)

    assert slot_target(legacy) is None
    assert slot_target(pool) == PoolTarget(9, 20)


async def test_scene_times_without_a_scene_are_empty_and_check_the_range() -> None:
    times = SceneTimes(make_group(), None)

    assert await times.between(MONDAY, MONDAY) == []
    with pytest.raises(DomainError) as error:
        await times.between(MONDAY, MONDAY - timedelta(days=1))
    assert error.value.code is ErrorCode.INVALID_RANGE
