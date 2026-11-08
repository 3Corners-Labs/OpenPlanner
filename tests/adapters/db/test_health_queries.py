"""SQL queries of connection health and reconciliation (ADR 0082 points 5 to 7)."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.calls import CallStatus, TaskState
from app.domain.connection_health import (
    HealthEntry,
    HealthState,
    InboundEvent,
    Platform,
)
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef
from app.domain.integration_check import CHECK_EVENT
from app.domain.request_log import REQUEST_EVENT
from app.domain.security_log import TOKEN_REFRESH_FAILED
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, SLOT_0900, TEST_TOKEN_KEY, book


def _event(
    action: str, outcome: Outcome, detail: str = "", reason: str = "", org: str | None = "demo"
) -> AuditEvent:
    return AuditEvent(NOW, SYSTEM_ACTOR, action, outcome, reason, org, None, detail, Source.SYSTEM)


async def test_samples_probes_and_token_failures_are_read(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.add_audit_event(
            _event(REQUEST_EVENT, Outcome.FAILURE, "method:POST,platform:wxcc")
        )
        await tx.add_audit_event(
            _event(REQUEST_EVENT, Outcome.SUCCESS, "method:GET,platform:webex")
        )
        await tx.add_audit_event(_event(REQUEST_EVENT, Outcome.SUCCESS, "method:GET,path:/old"))
        await tx.add_audit_event(_event(CHECK_EVENT, Outcome.FAILURE, reason="wxcc.probe.failed"))
        await tx.add_audit_event(_event(CHECK_EVENT, Outcome.FAILURE, reason="webex.host", org="x"))
        passed_later = _event(CHECK_EVENT, Outcome.FAILURE, reason="wxcc.probe.failed", org="y")
        await tx.add_audit_event(passed_later)
        await tx.add_audit_event(_event(CHECK_EVENT, Outcome.SUCCESS, reason="pass", org="y"))
    async with uow.read() as tx:
        samples = await tx.request_samples(NOW - timedelta(minutes=1))
        later = await tx.request_samples(NOW + timedelta(minutes=1))
        probes = await tx.failed_probe_organisations(NOW - timedelta(minutes=1))
        token = await tx.token_refresh_failed_since(NOW - timedelta(minutes=1))
    assert sorted((s.platform.value, s.failed) for s in samples) == [
        ("webex", False),
        ("wxcc", True),
    ]
    assert later == []
    assert probes == frozenset({"demo"})
    assert token is False
    async with uow.locked() as tx:
        await tx.add_audit_event(_event(TOKEN_REFRESH_FAILED, Outcome.FAILURE, org=None))
    async with uow.read() as tx:
        assert await tx.token_refresh_failed_since(NOW - timedelta(minutes=1))


async def test_health_and_inbound_events_round_trip(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    entries = (
        HealthEntry("demo", Platform.WXCC, HealthState.DOWN, NOW, 10, 9, "failures"),
        HealthEntry(None, Platform.WEBEX, HealthState.OK, NOW, 3, 0, "failures"),
    )
    async with uow.locked() as tx:
        await tx.save_health(entries, NOW)
        await tx.save_inbound_event(InboundEvent(Platform.WXCC, NOW, "task:ended", "recorded"))
    async with uow.read() as tx:
        assert await tx.get_health() == entries
        assert await tx.get_inbound_events() == (
            InboundEvent(Platform.WXCC, NOW, "task:ended", "recorded"),
        )


async def test_stale_tasks_carry_their_organisation_and_group(engine: AsyncEngine) -> None:
    await seed_group(engine, slug="aangifte")
    uow = SqlUnitOfWork(engine)
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    booking_id = receipt.booking.id or 0
    async with uow.locked() as tx:
        await tx.save_task(booking_id, ContactCenterTaskRef("task-1", ContactCenterKind.WEBEX_CC))
        await tx.save_call_status(
            CallStatus(
                booking_id, task=TaskState.QUEUED, task_created_at=SLOT_0900 - timedelta(minutes=5)
            )
        )
    async with uow.read() as tx:
        stale = await tx.stale_tasks(SLOT_0900 + timedelta(minutes=10), 50)
        early = await tx.stale_tasks(SLOT_0900 - timedelta(minutes=10), 50)
    assert [(t.booking_id, t.task.id, t.state) for t in stale] == [
        (booking_id, "task-1", TaskState.QUEUED)
    ]
    assert stale[0].organisation
    assert stale[0].group == "aangifte"
    assert early == []
