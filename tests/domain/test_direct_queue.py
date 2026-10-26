"""The group's own queue of direct calls (ADR 0026): phases, room, position and usual wait."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.calls import CallState, CallStatus, TaskState
from app.domain.direct_queue import (
    MIN_SAMPLES,
    SAMPLE_SIZE,
    DirectCall,
    QueuePhase,
    has_room,
    load_since,
    phase,
    queue_info,
    typical_wait,
)
from tests.support.domain import make_group

NOW = datetime(2026, 1, 5, 8, 30, tzinfo=UTC)
GROUP = make_group(direct_call_enabled=True, direct_call_max_concurrent=2)


def call(
    booking_id: int,
    *,
    minutes_ago: int = 1,
    task: TaskState = TaskState.QUEUED,
    active: bool = True,
    **status: object,
) -> DirectCall:
    """Return a direct call that started ``minutes_ago`` with the given status."""
    start = NOW - timedelta(minutes=minutes_ago)
    return DirectCall(
        booking_id=booking_id,
        start=start,
        end=start + timedelta(minutes=30),
        active=active,
        status=CallStatus(booking_id, task=task, **status),  # type: ignore[arg-type]
    )


def picked_up(booking_id: int, wait_minutes: int) -> DirectCall:
    """Return an ended call that waited ``wait_minutes`` for an agent, hours ago."""
    created = NOW - timedelta(hours=2, minutes=booking_id)
    return call(
        booking_id,
        minutes_ago=125,
        task=TaskState.ENDED,
        task_created_at=created,
        connected_at=created + timedelta(minutes=wait_minutes),
    )


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (call(1, task=TaskState.NONE), QueuePhase.WAITING),  # task not created yet
        (call(1), QueuePhase.WAITING),
        (call(1, minutes_ago=11), QueuePhase.DONE),  # not picked up after 10 min: stale
        (call(1, task=TaskState.CONNECTED, minutes_ago=50), QueuePhase.CONNECTED),
        (call(1, host_joined=True), QueuePhase.CONNECTED),  # agent in the meeting
        (call(1, task=TaskState.CONNECTED, minutes_ago=61), QueuePhase.DONE),  # lost end
        (call(1, task=TaskState.ENDED), QueuePhase.DONE),
        (call(1, task=TaskState.FAILED), QueuePhase.DONE),
        (call(1, task=TaskState.CANCELLED), QueuePhase.DONE),
        (call(1, task=TaskState.CONNECTED, call=CallState.ENDED), QueuePhase.DONE),
        (call(1, active=False), QueuePhase.DONE),  # cancelled booking
    ],
)
def test_phase_of_a_direct_call(given: DirectCall, expected: QueuePhase) -> None:
    assert phase(given, GROUP, NOW) is expected


def test_room_counts_waiting_and_connected_calls() -> None:
    connected = call(1, task=TaskState.CONNECTED)
    waiting = call(2)
    done = call(3, task=TaskState.ENDED)

    assert has_room(GROUP, [connected, done], NOW)
    assert not has_room(GROUP, [connected, waiting, done], NOW)
    assert has_room(replace(GROUP, direct_call_max_concurrent=None), [connected, waiting], NOW)


def test_load_window_covers_every_call_that_can_still_count() -> None:
    since = load_since(GROUP, NOW)

    assert since == NOW - timedelta(minutes=30) - timedelta(minutes=30)
    long_wait = replace(GROUP, not_picked_up_minutes=120)
    assert load_since(long_wait, NOW) == NOW - timedelta(minutes=120)


def test_position_counts_waiting_calls_that_started_earlier() -> None:
    calls = [
        call(1, minutes_ago=5),
        call(2, minutes_ago=4, task=TaskState.CONNECTED),  # with an agent: not ahead
        call(3, minutes_ago=3),
        call(4, minutes_ago=3),
        call(5, minutes_ago=12),  # stale: not ahead
    ]

    info = queue_info(calls, GROUP, NOW, booking_id=4)

    assert info.waiting == 3
    assert info.position == 3  # 1 and 3 are ahead; same start: lower id first
    assert queue_info(calls, GROUP, NOW, booking_id=2).position is None
    assert queue_info(calls, GROUP, NOW).position is None


def test_typical_wait_is_the_median_of_recent_pickups_rounded_up() -> None:
    calls = [picked_up(1, 2), picked_up(2, 7), picked_up(3, 3), call(4)]

    assert typical_wait(calls) == 3
    assert typical_wait([picked_up(1, 0)]) is None  # too few samples
    assert typical_wait([*calls[:3], picked_up(5, 0)]) == 3  # even count: upper middle
    assert MIN_SAMPLES == 3


def test_typical_wait_uses_only_the_latest_pickups() -> None:
    old = [picked_up(100 + i, 30) for i in range(SAMPLE_SIZE)]  # earlier, by id offset
    recent = [picked_up(i, 1) for i in range(1, SAMPLE_SIZE + 1)]

    assert typical_wait([*old, *recent]) == 1


def test_seconds_round_up_to_a_whole_minute() -> None:
    created = NOW - timedelta(hours=1)
    quick = [
        call(
            i,
            task=TaskState.ENDED,
            task_created_at=created,
            connected_at=created + timedelta(seconds=20),
        )
        for i in range(1, 4)
    ]

    assert typical_wait(quick) == 1
