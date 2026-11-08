"""Evaluating connection health and reconciling task states (ADR 0082 points 6 and 7)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.calls import CallStatus, EventKind, TaskSnapshot, TaskState
from app.domain.connection_health import (
    HEALTH_EVENT,
    RECONCILED,
    WEBHOOKS_MISSING,
    HealthPolicy,
    HealthState,
    InboundEvent,
    Platform,
)
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef
from app.domain.integration_check import CHECK_EVENT
from app.domain.integration_health import (
    IntegrationHealthService,
    IntegrationHealthWatch,
    TaskReconciler,
)
from app.domain.ports import IntegrationError
from app.domain.request_log import REQUEST_EVENT
from app.domain.security_log import TOKEN_REFRESH_FAILED
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, webex_cc
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.closures import confirmed_booking, three_groups
from tests.support.domain import SLOT_0900

NOW = SLOT_0900 + timedelta(hours=1)
POLICY = HealthPolicy(timedelta(minutes=15), 25, 75, timedelta(minutes=10))
EXPECTED_404 = "method:GET,path:/v1/tasks/x,status:404,ms:3,tracking:,version:1,platform:wxcc"


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(three_groups())


def request_event(failed: bool, platform: str = "wxcc", org: str = "demo") -> AuditEvent:
    return AuditEvent(
        NOW - timedelta(minutes=1),
        SYSTEM_ACTOR,
        REQUEST_EVENT,
        Outcome.FAILURE if failed else Outcome.SUCCESS,
        "server_error" if failed else "",
        org,
        "demo",
        f"method:POST,path:/v2/tasks,status:500,ms:3,tracking:,version:1,platform:{platform}",
        Source.SYSTEM,
    )


def events(uow: InMemoryUnitOfWork, action: str) -> list[AuditEvent]:
    return [e for e in uow.state.audit if e.action == action]


async def test_failures_turn_a_connection_down_and_write_one_event(
    uow: InMemoryUnitOfWork,
) -> None:
    service = IntegrationHealthService(uow, FixedClock(NOW), POLICY)
    uow.state.audit += [request_event(True) for _ in range(4)] + [request_event(False)]
    (entry,) = await service.evaluate()
    assert (entry.organisation, entry.platform, entry.state) == (
        "demo",
        Platform.WXCC,
        HealthState.DOWN,
    )
    await service.evaluate()  # unchanged: no second event
    (event,) = events(uow, HEALTH_EVENT)
    assert (event.outcome, event.reason, event.organisation) == (Outcome.FAILURE, "down", "demo")
    assert "Ada" not in event.detail


async def test_a_token_refresh_failure_takes_every_connection_down(
    uow: InMemoryUnitOfWork,
) -> None:
    uow.state.audit += [request_event(False) for _ in range(5)]
    uow.state.audit.append(
        AuditEvent(
            NOW,
            SYSTEM_ACTOR,
            TOKEN_REFRESH_FAILED,
            Outcome.FAILURE,
            "unauthorized",
            source=Source.SYSTEM,
        )
    )
    (entry,) = await IntegrationHealthService(uow, FixedClock(NOW), POLICY).evaluate()
    assert (entry.state, entry.reason) == (HealthState.DOWN, "token_refresh")


async def test_a_failed_probe_takes_the_organisations_contact_center_down(
    uow: InMemoryUnitOfWork,
) -> None:
    uow.state.audit.append(
        AuditEvent(
            NOW,
            SYSTEM_ACTOR,
            CHECK_EVENT,
            Outcome.FAILURE,
            "wxcc.probe.no_events",
            "demo",
            "demo",
            source=Source.SYSTEM,
        )
    )
    (entry,) = await IntegrationHealthService(uow, FixedClock(NOW), POLICY).evaluate()
    assert (entry.platform, entry.state, entry.reason) == (Platform.WXCC, HealthState.DOWN, "probe")


async def test_an_expected_404_of_a_task_read_is_no_failure(uow: InMemoryUnitOfWork) -> None:
    gone = replace(request_event(True), reason="rejected_404", detail=EXPECTED_404)
    uow.state.audit += [gone] * 5
    (entry,) = await IntegrationHealthService(uow, FixedClock(NOW), POLICY).evaluate()
    assert entry.state is HealthState.OK


async def test_old_requests_fall_out_of_the_window(uow: InMemoryUnitOfWork) -> None:
    uow.state.audit += [replace(request_event(True), at=NOW - timedelta(hours=1))] * 5
    assert await IntegrationHealthService(uow, FixedClock(NOW), POLICY).evaluate() == ()


async def test_the_last_inbound_event_is_kept_per_platform(uow: InMemoryUnitOfWork) -> None:
    service = IntegrationHealthService(uow, FixedClock(NOW), POLICY)
    await service.note_inbound(InboundEvent(Platform.WXCC, NOW, "task:ended", "recorded"))
    later = NOW + timedelta(seconds=3)  # within NOTE_EVERY: not written again
    await service.note_inbound(InboundEvent(Platform.WXCC, later, "task:new", "ignored"))
    _, inbound = await service.overview()
    assert [e.kind for e in inbound] == ["task:ended"]
    later = NOW + timedelta(seconds=11)
    await service.note_inbound(InboundEvent(Platform.WXCC, later, "task:connected", "duplicate"))
    await service.note_inbound(InboundEvent(Platform.WEBEX, NOW, "meetings:started", "unknown"))
    _, inbound = await service.overview()
    assert [(e.platform, e.kind) for e in inbound] == [
        (Platform.WEBEX, "meetings:started"),
        (Platform.WXCC, "task:connected"),
    ]


def with_task(uow: InMemoryUnitOfWork, task: TaskState, booking_id: int = 10) -> None:
    uow.state.bookings[booking_id] = confirmed_booking(
        id=booking_id,
        contact_center_task=ContactCenterTaskRef(f"t-{booking_id}", ContactCenterKind.WEBEX_CC),
    )
    uow.state.call_status[booking_id] = CallStatus(
        booking_id, task=task, task_created_at=SLOT_0900 - timedelta(minutes=5)
    )


class Recorder:
    """Stands in for ``IntegrationService.record``: keeps what the reconcile writes."""

    def __init__(self) -> None:
        self.written: list[tuple[int, EventKind, str]] = []

    async def record(self, booking_id: int, kind: EventKind, *, detail: str = "") -> None:
        self.written.append((booking_id, kind, detail))


def reconciler(
    uow: InMemoryUnitOfWork, dispatcher: FakeDispatcher | None, clock: FixedClock | None = None
) -> tuple[TaskReconciler, list[tuple[int, EventKind, str]]]:
    recorder = Recorder()
    run = TaskReconciler(
        uow,
        clock or FixedClock(NOW),
        webex_cc(dispatcher) if dispatcher else {},
        recorder,
        timedelta(minutes=10),  # type: ignore[arg-type]
    )
    return run, recorder.written


async def test_a_missed_state_is_written_and_reported(uow: InMemoryUnitOfWork) -> None:
    with_task(uow, TaskState.QUEUED)
    dispatcher = FakeDispatcher()
    dispatcher.snapshots["t-10"] = TaskSnapshot(TaskState.ENDED, "ended")
    run, written = reconciler(uow, dispatcher)
    assert await run.run() == 1
    assert written == [(10, EventKind.TASK_ENDED, RECONCILED)]
    (event,) = events(uow, WEBHOOKS_MISSING)
    assert (event.outcome, event.reason, event.detail, event.organisation) == (
        Outcome.FAILURE,
        "missed",
        "count:1",
        "demo",
    )


async def test_a_clean_run_after_a_miss_closes_the_alert(uow: InMemoryUnitOfWork) -> None:
    with_task(uow, TaskState.QUEUED)
    dispatcher = FakeDispatcher()
    dispatcher.snapshots["t-10"] = TaskSnapshot(TaskState.CONNECTED, "connected")
    clock = FixedClock(NOW)
    run, _ = reconciler(uow, dispatcher, clock)
    await run.run()
    uow.state.call_status[10] = replace(uow.state.call_status[10], task=TaskState.CONNECTED)
    assert await run.run() == 0  # read in the last run: skipped, and nothing open is closed
    assert len(events(uow, WEBHOOKS_MISSING)) == 1
    clock.advance(timedelta(hours=2))
    assert await run.run() == 0
    assert [e.outcome for e in events(uow, WEBHOOKS_MISSING)] == [Outcome.FAILURE, Outcome.SUCCESS]
    await run.run()  # nothing open any more: no further event
    assert len(events(uow, WEBHOOKS_MISSING)) == 2


async def test_unreadable_or_unknown_tasks_are_left_alone(uow: InMemoryUnitOfWork) -> None:
    with_task(uow, TaskState.QUEUED)
    with_task(uow, TaskState.ENDED, booking_id=11)  # not watched
    dispatcher = FakeDispatcher()
    clock = FixedClock(NOW)
    run, written = reconciler(uow, dispatcher, clock)
    assert await run.run() == 0  # NONE: WxCC does not know it
    assert dispatcher.read == ["t-10"]
    assert await run.run() == 0  # read in the last run: not read again yet
    assert dispatcher.read == ["t-10"]
    clock.advance(timedelta(hours=2))
    dispatcher.read_error = IntegrationError("unreachable", retryable=True)
    assert await run.run() == 0
    assert written == []
    assert events(uow, WEBHOOKS_MISSING) == []
    assert await reconciler(uow, None)[0].run() == 0


async def test_a_read_carries_the_bookings_trace(uow: InMemoryUnitOfWork) -> None:
    """The reconcile's request lands at the booking and its organisation (ADR 0068)."""
    from app.domain.event_context import current

    with_task(uow, TaskState.QUEUED)
    seen = []

    class Tracing(FakeDispatcher):
        async def read_task(self, task_id: str) -> TaskSnapshot:
            context = current()
            seen.append((context.booking_id, context.organisation, context.group))
            return await super().read_task(task_id)

    await reconciler(uow, Tracing())[0].run()
    assert seen == [(10, "demo", "demo")]


async def test_tasks_read_before_make_room_for_newer_ones(uow: InMemoryUnitOfWork) -> None:
    from app.domain.connection_health import RECONCILE_LIMIT

    for booking_id in range(10, 10 + RECONCILE_LIMIT + 5):
        with_task(uow, TaskState.QUEUED, booking_id)
    dispatcher = FakeDispatcher()
    run, _ = reconciler(uow, dispatcher)
    await run.run()
    await run.run()
    assert len(dispatcher.read) == RECONCILE_LIMIT + 5
    assert len(set(dispatcher.read)) == RECONCILE_LIMIT + 5


async def test_a_recent_task_is_not_read_yet(uow: InMemoryUnitOfWork) -> None:
    with_task(uow, TaskState.QUEUED)
    uow.state.bookings[10] = replace(uow.state.bookings[10], start=NOW - timedelta(minutes=5))
    dispatcher = FakeDispatcher()
    await reconciler(uow, dispatcher)[0].run()
    assert dispatcher.read == []


async def test_the_watch_runs_on_its_interval(uow: InMemoryUnitOfWork) -> None:
    clock = FixedClock(NOW)
    run, _ = reconciler(uow, FakeDispatcher())
    watch = IntegrationHealthWatch(
        run, IntegrationHealthService(uow, clock, POLICY), clock, timedelta(minutes=5)
    )
    assert await watch.run()
    assert not await watch.run()
    clock.advance(timedelta(minutes=6))
    assert await watch.run()
