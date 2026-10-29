"""Post-commit emit of audit events (ADR 0034, AU-1): once after commit, never after rollback."""

from collections.abc import Sequence
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import AuditEvent, Outcome, Source
from app.domain.authorization import Scope
from tests.support.domain import NOW


class RecordingSink:
    def __init__(self) -> None:
        self.batches: list[list[AuditEvent]] = []

    def emit(self, events: Sequence[AuditEvent]) -> None:
        self.batches.append(list(events))


def _event(action: str) -> AuditEvent:
    return AuditEvent(NOW, "sub-1", action, Outcome.SUCCESS)


async def test_events_are_emitted_once_after_commit(engine: AsyncEngine) -> None:
    sink = RecordingSink()
    uow = SqlUnitOfWork(engine, sink=sink)
    first, second = _event("group.edit"), _event("admin.logout")

    async with uow.locked() as tx:
        await tx.add_audit_event(first)
        await tx.add_audit_event(second)
        assert sink.batches == []  # nothing before the commit

    assert sink.batches == [[first, second]]


async def test_a_rolled_back_event_is_never_emitted(engine: AsyncEngine) -> None:
    sink = RecordingSink()
    uow = SqlUnitOfWork(engine, sink=sink)

    async def refused_change() -> None:
        async with uow.locked() as tx:
            await tx.add_audit_event(_event("group.edit"))
            raise RuntimeError("change refused")

    with pytest.raises(RuntimeError):
        await refused_change()

    assert sink.batches == []
    async with uow.read() as tx:
        assert await tx.list_audit_events(Scope(everything=True)) == []


async def test_a_transaction_without_events_emits_nothing(engine: AsyncEngine) -> None:
    sink = RecordingSink()

    async with SqlUnitOfWork(engine, sink=sink).locked() as tx:
        await tx.delete_audit_events(Source.SYSTEM, NOW - timedelta(days=1))
    async with SqlUnitOfWork(engine, sink=sink).read() as tx:
        await tx.list_audit_events(Scope(everything=True))

    assert sink.batches == []
