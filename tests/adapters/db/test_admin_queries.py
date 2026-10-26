"""Scoped admin queries (ADR 0014): the SQL filter is the database side of decide()."""

import inspect
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import (
    SYSTEM_ACTOR,
    AdminProfile,
    AuditEntry,
    AuditEvent,
    AuditFilter,
    Outcome,
    Source,
)
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


def _system(at_offset: int, outcome: Outcome, org: str | None, group: str | None) -> AuditEvent:
    return AuditEvent(
        NOW + timedelta(seconds=at_offset),
        SYSTEM_ACTOR,
        "job.send_reminder",
        outcome,
        "timeout" if outcome is Outcome.RETRY else "",
        org,
        group,
        "attempt:2,channel:sms",
        Source.SYSTEM,
        42 if group else None,
    )


async def test_audit_scope_is_exact_per_level(engine: AsyncEngine) -> None:
    # ADR 0029: global, organisation and group events; each admin sees their level and below.
    uow = SqlUnitOfWork(engine)
    global_event = _system(0, Outcome.FAILURE, None, None)
    org_event = AuditEvent(
        NOW + timedelta(seconds=1), "o", "holidays.assign", Outcome.SUCCESS, organisation="demo"
    )
    group_event = _system(2, Outcome.RETRY, "demo", "demo")
    other_group = _system(3, Outcome.SUCCESS, "andere", "andere-team")
    async with uow.locked() as tx:
        for event in (global_event, org_event, group_event, other_group):
            await tx.add_audit_event(event)

    async with uow.read() as tx:
        group_admin = await tx.list_audit_events(Scope(groups=frozenset({"demo"})))
        org_admin = await tx.list_audit_events(Scope(organisations=frozenset({"demo"})))
        superuser = await tx.list_audit_events(EVERYTHING)

    assert [e.event for e in group_admin] == [group_event]
    assert [e.event for e in org_admin] == [group_event, org_event]
    assert len(superuser) == 4


@pytest.mark.parametrize(
    ("narrow", "expected"),
    [
        (AuditFilter(source=Source.SYSTEM), [3, 1]),
        (AuditFilter(source=Source.ADMIN), [2]),
        (AuditFilter(problems_only=True), [3, 2]),
        (AuditFilter(group="demo"), [3, 2, 1]),
        (AuditFilter(booking_id=42), [3, 1]),
        (AuditFilter(group="andere-team"), []),  # outside the scope stays outside
    ],
)
async def test_audit_filter_narrows_within_the_scope(
    engine: AsyncEngine, narrow: AuditFilter, expected: list[int]
) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.add_audit_event(_system(1, Outcome.SUCCESS, "demo", "demo"))
        await tx.add_audit_event(
            AuditEvent(
                NOW + timedelta(seconds=2),
                "o",
                "group.view",
                Outcome.DENIED,
                "out_of_scope",
                "demo",
                "demo",
            )
        )
        await tx.add_audit_event(_system(3, Outcome.RETRY, "demo", "demo"))
        await tx.add_audit_event(_system(4, Outcome.FAILURE, "andere", "andere-team"))

    async with uow.read() as tx:
        found = await tx.list_audit_events(Scope(organisations=frozenset({"demo"})), 100, narrow)

    assert [int((e.event.at - NOW).total_seconds()) for e in found] == expected


async def test_old_system_events_are_deleted_admin_actions_kept(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.add_audit_event(_system(0, Outcome.SUCCESS, "demo", "demo"))
        await tx.add_audit_event(AuditEvent(NOW, "o", "group.edit", Outcome.SUCCESS))
        await tx.add_audit_event(_system(10, Outcome.SUCCESS, "demo", "demo"))

    async with uow.locked() as tx:
        removed = await tx.delete_audit_events(Source.SYSTEM, NOW + timedelta(seconds=5))
    async with uow.read() as tx:
        left = await tx.list_audit_events(EVERYTHING)

    assert removed == 1
    assert [(e.event.source, e.event.booking_id) for e in left] == [
        (Source.SYSTEM, 42),
        (Source.ADMIN, None),
    ]


def test_every_listing_query_requires_a_scope() -> None:
    # ADR 0014: a new list_* method without a Scope parameter fails this test.
    listings = [name for name in dir(Transaction) if name.startswith("list_")]

    assert listings, "no listing queries found"
    for name in listings:
        parameters = inspect.signature(getattr(Transaction, name)).parameters
        assert "scope" in parameters, name
        assert parameters["scope"].annotation in (Scope, "Scope"), name
        assert parameters["scope"].default is inspect.Parameter.empty, name
