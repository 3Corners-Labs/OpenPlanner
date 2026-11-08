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
from tests.fakes.integrations import FakeDispatcher
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.closures import confirmed_booking, three_groups
from tests.support.domain import SLOT_0900

NOW = SLOT_0900 + timedelta(hours=1)
POLICY = HealthPolicy(timedelta(minutes=15), 25, 75, timedelta(minutes=10))


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


async def test_old_requests_fall_out_of_the_window(uow: InMemoryUnitOfWork) -> None:
    uow.state.audit += [replace(request_event(True), at=NOW - timedelta(hours=1))] * 5
    assert await IntegrationHealthService(uow, FixedClock(NOW), POLICY).evaluate() == ()


async def test_the_last_inbound_event_is_kept_per_platform(uow: InMemoryUnitOfWork) -> None:
    service = IntegrationHealthService(uow, FixedClock(NOW), POLICY)
    await service.note_inbound(InboundEvent(Platform.WXCC, NOW, "task:ended", "recorded"))
    await service.note_inbound(InboundEvent(Platform.WXCC, NOW, "task:connected", "duplicate"))
    await service.note_inbound(InboundEvent(Platform.WEBEX, NOW, "meetings:started", "unknown"))
    _, inbound = await service.overview()
    assert [(e.platform, e.kind) for e in inbound] == [
        (Platform.WEBEX, "meetings:started"),
        (Platform.WXCC, "task:connected"),
    ]


def with_task(uow: InMemoryUnitOfWork, task: TaskState, booking_id: int = 10) -> None:
    uow.state.bookings[booking_id] = confirmed_booking(
        id=booking_id, wxcc_task_id=f"t-{booking_id}"
    )
    uow.state.call_status[booking_id] = CallStatus(
        booking_id, task=task, task_created_at=SLOT_0900 - timedelta(minutes=5)
    )


def reconciler(
    uow: InMemoryUnitOfWork, dispatcher: FakeDispatcher | None
) -> tuple[TaskReconciler, list[tuple[int, EventKind, str]]]:
    written: list[tuple[int, EventKind, str]] = []

    async def record(booking_id: int, kind: EventKind, detail: str) -> None:
        written.append((booking_id, kind, detail))

    return TaskReconciler(uow, FixedClock(NOW), dispatcher, record, timedelta(minutes=10)), written


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
    run, _ = reconciler(uow, dispatcher)
    await run.run()
    uow.state.call_status[10] = replace(uow.state.call_status[10], task=TaskState.CONNECTED)
    assert await run.run() == 0
    assert [e.outcome for e in events(uow, WEBHOOKS_MISSING)] == [Outcome.FAILURE, Outcome.SUCCESS]
    await run.run()  # nothing open any more: no further event
    assert len(events(uow, WEBHOOKS_MISSING)) == 2


async def test_unreadable_or_unknown_tasks_are_left_alone(uow: InMemoryUnitOfWork) -> None:
    with_task(uow, TaskState.QUEUED)
    with_task(uow, TaskState.ENDED, booking_id=11)  # not watched
    dispatcher = FakeDispatcher()
    run, written = reconciler(uow, dispatcher)
    assert await run.run() == 0  # NONE: WxCC does not know it
    assert dispatcher.read == ["t-10"]
    dispatcher.read_error = IntegrationError("unreachable", retryable=True)
    assert await run.run() == 0
    assert written == []
    assert events(uow, WEBHOOKS_MISSING) == []
    assert await reconciler(uow, None)[0].run() == 0


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
