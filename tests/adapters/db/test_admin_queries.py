"""Scoped admin queries (ADR 0014): the SQL filter is the database side of decide()."""

import inspect
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import AdminProfile, AuditEntry, AuditEvent, Outcome
from app.domain.authorization import Scope
from app.domain.ports import Transaction
from tests.support.domain import NOW

EVERYTHING = Scope(everything=True)


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (EVERYTHING, ["andere-team", "demo"]),
        (Scope(organisations=frozenset({"demo"})), ["demo"]),
        (Scope(groups=frozenset({"andere-team"})), ["andere-team"]),
        (
            Scope(organisations=frozenset({"demo"}), groups=frozenset({"andere-team"})),
            ["andere-team", "demo"],
        ),
        (Scope(organisations=frozenset({"nope"})), []),
        (Scope(), []),
    ],
)
async def test_list_groups_respects_the_scope(
    engine: AsyncEngine, scope: Scope, expected: list[str]
) -> None:
    await seed_demo(engine)

    async with SqlUnitOfWork(engine).read() as tx:
        groups = await tx.list_groups(scope)

    assert [g.group.slug for g in groups] == expected


async def test_group_summary_carries_the_organisation(engine: AsyncEngine) -> None:
    await seed_demo(engine)

    async with SqlUnitOfWork(engine).read() as tx:
        summary = await tx.get_group_summary("andere-team")
        missing = await tx.get_group_summary("nope")

    assert summary is not None
    assert (summary.organisation_slug, summary.organisation_name) == ("andere", "Andere gemeente")
    assert missing is None


async def test_audit_events_are_stored_and_scoped(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    events = [
        AuditEvent(NOW, "sub-1", "admin.login", Outcome.SUCCESS),
        AuditEvent(
            NOW + timedelta(seconds=1),
            "sub-1",
            "group.view",
            Outcome.DENIED,
            "out_of_scope",
            "andere",
            "andere-team",
        ),
        AuditEvent(
            NOW + timedelta(seconds=2),
            "sub-2",
            "group.view",
            Outcome.SUCCESS,
            "group",
            "demo",
            "demo",
        ),
    ]
    async with uow.locked() as tx:
        for event in events:
            await tx.add_audit_event(event)

    async with uow.read() as tx:
        everything = await tx.list_audit_events(EVERYTHING)
        demo = await tx.list_audit_events(Scope(organisations=frozenset({"demo"})))
        limited = await tx.list_audit_events(EVERYTHING, limit=1)

    assert [e.event.action for e in everything] == ["group.view", "group.view", "admin.login"]
    assert everything[0] == AuditEntry(events[2])
    assert demo == [AuditEntry(events[2])]
    assert limited == [AuditEntry(events[2])]


async def test_audit_entries_carry_the_latest_profile_name(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.add_audit_event(AuditEvent(NOW, "sub-1", "admin.login", Outcome.SUCCESS))
        await tx.add_audit_event(AuditEvent(NOW, "sub-2", "admin.login", Outcome.SUCCESS))
        await tx.save_admin_profile(AdminProfile("sub-1", "Old Name", NOW))
    async with uow.locked() as tx:
        await tx.save_admin_profile(AdminProfile("sub-1", "Gina Groep", NOW + timedelta(hours=1)))

    async with uow.read() as tx:
        entries = await tx.list_audit_events(EVERYTHING)

    assert {e.event.actor: e.actor_name for e in entries} == {"sub-1": "Gina Groep", "sub-2": None}


def test_every_listing_query_requires_a_scope() -> None:
    # ADR 0014: a new list_* method without a Scope parameter fails this test.
    listings = [name for name in dir(Transaction) if name.startswith("list_")]

    assert listings, "no listing queries found"
    for name in listings:
        parameters = inspect.signature(getattr(Transaction, name)).parameters
        assert "scope" in parameters, name
        assert parameters["scope"].annotation in (Scope, "Scope"), name
        assert parameters["scope"].default is inspect.Parameter.empty, name
