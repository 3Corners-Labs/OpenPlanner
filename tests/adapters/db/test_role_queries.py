"""Storage of custom roles (ADR 0039): round trip, unique claim value, unknown values."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import CustomRole, Permission, Role
from tests.support.domain import NOW

VIEW = frozenset({Permission.DASHBOARD_VIEW, Permission.GROUP_VIEW})


def role(claim_value: str = "openplanner-lead") -> CustomRole:
    return CustomRole(0, Role.GROUP_ADMIN, VIEW, claim_value, "Teamleider", "Team lead")


async def test_a_role_round_trips(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        stored = await tx.add_custom_role(role())
    async with uow.locked() as tx:
        await tx.save_custom_role(
            CustomRole(
                stored.id,
                stored.template,
                VIEW | {Permission.GROUP_EDIT},
                "openplanner-lead-2",
                "Leider",
                "Lead",
                2,
            )
        )
    async with uow.read() as tx:
        found = await tx.get_custom_role(stored.id)
        listed = await tx.list_custom_roles()

    assert found == CustomRole(
        stored.id,
        Role.GROUP_ADMIN,
        VIEW | {Permission.GROUP_EDIT},
        "openplanner-lead-2",
        "Leider",
        "Lead",
        2,
    )
    assert listed == [found]

    async with uow.locked() as tx:
        await tx.delete_custom_role(stored.id)
    async with uow.read() as tx:
        assert await tx.get_custom_role(stored.id) is None
        assert await tx.list_custom_roles() == []


async def test_a_claim_value_is_unique_in_the_database_too(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.add_custom_role(role())
    with pytest.raises(IntegrityError):
        async with uow.locked() as tx:
            await tx.add_custom_role(role())


async def test_unknown_stored_values_grant_nothing(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO roles (template, name_nl, name_en, claim_value, permissions, version)"
                " VALUES ('group_admin', 'A', 'A', 'a', 'group.view nope.manage', 1),"
                " ('wizard', 'B', 'B', 'b', 'group.view', 1)"
            )
        )
    async with SqlUnitOfWork(engine).read() as tx:
        roles = await tx.list_custom_roles()

    assert [(r.claim_value, r.permissions) for r in roles] == [
        ("a", frozenset({Permission.GROUP_VIEW}))
    ]


async def test_save_of_an_unknown_role_fails(engine: AsyncEngine) -> None:
    with pytest.raises(LookupError):
        async with SqlUnitOfWork(engine).locked() as tx:
            await tx.save_custom_role(CustomRole(99, Role.ORG_ADMIN, VIEW, "x", "X", "X"))


async def test_list_events_returns_only_the_asked_actions_newest_first(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        for action in ("role.created", "group.edit", "role.updated"):
            await tx.add_audit_event(AuditEvent(NOW, "s-1", action, Outcome.SUCCESS))
    async with uow.read() as tx:
        events = await tx.list_events(("role.created", "role.updated"), 10)

    assert [e.event.action for e in events] == ["role.updated", "role.created"]
