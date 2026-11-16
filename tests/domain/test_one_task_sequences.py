"""Random sequences keep one task, of one kind, per contact moment (ADR 0123 point 8).

A property test without a new dependency (``hypothesis`` is not one of ours): a fixed list of
seeds drives ``random.Random`` through sequences of the steps that can make a second task or a
task of the wrong kind: dispatch (the job, its retries and the fallback's fresh job), a create
whose outcome is unknown or refused, the fence round, the watchdog's end of the booking (cancel)
and a move (the old booking cancelled), a change of the service's task kind, and late platform
events of either kind, the kind named as the platform names it. After every step:

* of the tasks OpenPlanner made or took on for the booking (its creates, and late tasks it
  attached), at most one is live at the platform (not ended by the end-task job);
* every create the platform made is the booking's task: none is left behind unknown;
* the task the booking holds has the kind the platform made it as, whatever the routing says now.

A failing seed reproduces with ``SEEDS = [seed]``; the step list is printed by the assertion.
"""

import random
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.booking import BookingStatus
from app.domain.calls import EventKind
from app.domain.contact_center import TaskKind
from app.domain.job_plan import JobType
from app.domain.ports import IntegrationError
from app.domain.retention_sweep import HELD_TWICE, RetentionSweep
from app.domain.scheduling import DispatchKind
from tests.fakes.clock import FixedClock
from tests.support.contact_center_requests import (
    AMBIGUOUS,
    CC_FLOW,
    WINDOW,
    Desk,
    cc_world,
    desk,
    dispatch,
    end_jobs,
    ensure,
    event,
    fenced,
    routed,
)
from tests.support.request_work import AGENT, at, book

SEEDS = list(range(40))
STEPS = 25
STEP_NAMES = (
    "dispatch",
    "dispatch_unknown",
    "dispatch_refused",
    "fence",
    "late_event",
    "cancel",
    "change_kind",
    "end_jobs",
    "wait",
)


class Platform:
    """What the platform made: each create the fake dispatcher accepted, and each late task."""

    def __init__(self, the_desk: Desk) -> None:
        self.desk = the_desk
        self.kinds: dict[str, TaskKind] = {}
        """Every task the platform holds, by id, with the kind it was made as."""
        self.late = 0
        self.attached: set[str] = set()
        """Every task the booking held at some point: OpenPlanner's to end."""

    def made(self) -> None:
        """Note the creates the fake dispatcher accepted since the last look."""
        for number, kind in enumerate(self.desk.dispatcher.kinds, start=1):
            self.kinds.setdefault(f"task-{number}", kind)

    def late_task(self, kind: TaskKind) -> str:
        """Make a task the platform reports late (an unknown outcome that did create)."""
        self.late += 1
        task_id = f"late-{self.late}"
        self.kinds[task_id] = kind
        return task_id

    def live(self) -> set[str]:
        """The tasks not ended by the end-task job."""
        return set(self.kinds) - set(self.desk.dispatcher.ended)


async def step(name: str, rng: random.Random, the_desk: Desk, booking_id: int, p: Platform) -> None:
    """Apply one step of the sequence to ``booking_id``."""
    uow, clock = the_desk.uow, the_desk.clock
    booking = uow.state.bookings[booking_id]
    if name.startswith("dispatch") and booking.status is BookingStatus.CONFIRMED:
        the_desk.dispatcher.error = {
            "dispatch": None,
            "dispatch_unknown": AMBIGUOUS,
            "dispatch_refused": IntegrationError("rejected_400", retryable=False),
        }[name]
        try:
            if booking.flow is None:
                await ensure(the_desk, booking_id)
            else:
                await dispatch(the_desk, booking_id)
        except IntegrationError:
            pass
        the_desk.dispatcher.error = None
    elif name == "fence":
        await the_desk.dispatch.fence_round()
    elif name == "late_event":
        kind = rng.choice((TaskKind.FORM, TaskKind.CONVERSATION))
        # The platform names a task's kind in its events (``channelType``); an event without it
        # falls back to the kind stored with the attempt, which only that attempt's own task
        # can match (``test_one_task_per_contact_moment``), so it is left out here.
        named = kind
        await event(the_desk, p.late_task(kind), EventKind.TASK_CREATED, booking.reference, named)
    elif name == "cancel":
        uow.state.bookings[booking_id] = replace(booking, status=BookingStatus.CANCELLED)
        await the_desk.calls.end_task(uow.state.bookings[booking_id], uow.state.groups[1])
    elif name == "change_kind":
        routed(uow, rng.choice((TaskKind.FORM, TaskKind.CONVERSATION)))
    elif name == "end_jobs" and any(
        j.type is JobType.END_WXCC_TASK for j in uow.state.jobs.get(booking_id, [])
    ):
        await end_jobs(the_desk, booking_id)
        jobs = uow.state.jobs[booking_id]
        uow.state.jobs[booking_id] = [j for j in jobs if j.type is not JobType.END_WXCC_TASK]
    elif name == "wait":
        clock.advance(timedelta(minutes=rng.choice((1, WINDOW, WINDOW + 1))))
    p.made()
    held = uow.state.bookings[booking_id].contact_center_task
    if held is not None:
        p.attached.add(held.id)


def check(the_desk: Desk, booking_id: int, p: Platform, trail: list[str]) -> None:
    """The invariant of ADR 0123 point 8 after a step."""
    task = the_desk.uow.state.bookings[booking_id].contact_center_task
    live = p.live()
    creates = {t for t in p.kinds if t.startswith("task-")}
    assert len(live & (creates | p.attached)) <= 1, trail
    assert creates <= p.attached, trail
    if task is not None:
        assert task.task_kind is p.kinds[task.id], trail


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("flow", [CC_FLOW, None], ids=["request", "legacy"])
async def test_one_task_of_one_kind_per_contact_moment(
    seed: int, flow: object, clock: FixedClock
) -> None:
    rng = random.Random(seed)  # noqa: S311 - a test's reproducible sequence, not a secret
    uow = cc_world()
    the_desk = desk(uow, clock, uses_call_page=True)
    first = rng.choice((TaskKind.FORM, TaskKind.CONVERSATION))
    routed(uow, first, dispatch_kind=DispatchKind.CONTACT_CENTER if flow is None else None)
    booking_id = await book(uow, clock, flow)  # type: ignore[arg-type]
    platform = Platform(the_desk)
    trail: list[str] = []
    for _ in range(STEPS):
        name = rng.choice(STEP_NAMES)
        trail.append(name)
        await step(name, rng, the_desk, booking_id, platform)
        check(the_desk, booking_id, platform, trail)


async def test_the_nightly_sweep_reports_a_booking_held_twice(clock: FixedClock) -> None:
    """A late task kept to be ended while the own queue claimed the request: counted, as a
    count only, and the alert closes once the platform reports the task over."""
    uow = cc_world()
    the_desk = desk(uow, clock)
    booking_id = await book(uow, clock, CC_FLOW)
    await fenced(the_desk, booking_id)
    reference = uow.state.bookings[booking_id].reference
    await event(the_desk, "late-1", EventKind.TASK_CREATED, reference)
    await event(the_desk, "late-1", EventKind.TASK_CONNECTED, reference)
    at(clock)
    await the_desk.queue.claim(AGENT, "demo", booking_id)
    sweep = RetentionSweep(uow, clock)

    held = await sweep.run()
    await event(the_desk, "late-1", EventKind.TASK_ENDED, reference)
    clear = await sweep.run()

    assert (held.held_twice, clear.held_twice) == (1, 0)
    events = [(e.outcome.value, e.detail) for e in uow.state.audit if e.action == HELD_TWICE]
    assert events == [("failure", "bookings:1"), ("success", "bookings:0")]
