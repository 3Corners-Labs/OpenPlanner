"""The world the flow contract tests run in, on the SQL adapter and on the fake (ADR 0121).

Two organisations (``acme`` and ``beta``), a group and a staff member in each, and a person who
belongs to both, seeded through the ``Transaction`` methods; ``open_world`` yields them on either
backend together with the ways to reach behind the port (who granted a skill, archive a pool, clear
a pool's code) for the states the port refuses to produce. The ``world`` fixture is in
``tests/adapters/db/conftest.py``.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import CapacityPoolRow, StaffSkillRow
from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import GroupRouting, Subject
from app.domain.scheduling import Organisation
from app.domain.staff import Membership, StaffMember
from tests.support.domain import NOW, make_group
from tests.support.flow_backends import open_backend

EVERYTHING = Scope(everything=True)
LATER = datetime(2026, 2, 1, 9, 0, tzinfo=UTC)


@dataclass
class World:
    """Two organisations with a group and a staff member each, on either backend."""

    uow: Any
    acme: int
    beta: int
    acme_group: int
    beta_group: int
    acme_staff: int
    beta_staff: int
    both_staff: int
    granted_by: Callable[[int, int], Awaitable[int | None]] | None
    """Who granted a skill; only the SQL variant can read it (the port does not return it)."""
    archive_pool_directly: Callable[[int], Awaitable[None]]
    """Archive a pool behind the port's back, for a state the port refuses to produce."""
    forget_pool_code: Callable[[int], Awaitable[None]]
    """Clear a pool's code behind the port's back: a row written before migration 0110."""


def person(subject: str, organisations: list[str]) -> StaffMember:
    """Return a staff member with an organisation admin membership in each organisation."""
    return StaffMember(
        subject,
        "Staff",
        frozenset(),
        frozenset({Membership("org_admin", slug) for slug in organisations}),
        NOW,
        NOW,
        NOW,
    )


async def _seed(uow: Any) -> tuple[int, ...]:
    """Seed through the port; return the ids of the organisations, groups and staff."""
    async with uow.locked() as tx:
        acme = await tx.add_organisation(Organisation(0, "Acme", "acme"))
        beta = await tx.add_organisation(Organisation(0, "Beta", "beta"))
        a_group = await tx.add_group(make_group(id=0, organisation_id=acme.id, slug="balie"))
        b_group = await tx.add_group(make_group(id=0, organisation_id=beta.id, slug="loket"))
        await tx.save_group_routing(
            a_group.id,
            GroupRouting(
                subjects=(
                    Subject.create("id", {"nl": "Id", "en": "Id"}),
                    Subject.create("visa", {"nl": "Visum", "en": "Visa"}),
                )
            ),
        )
        for subject, slugs in (
            ("sub-a", ["acme"]),
            ("sub-b", ["beta"]),
            ("sub-ab", ["acme", "beta"]),
        ):
            await tx.save_staff(person(subject, slugs))
        ids = []
        for subject in ("sub-a", "sub-b", "sub-ab"):
            member = await tx.get_staff(subject)
            assert member is not None
            ids.append(member.id)
    return acme.id, beta.id, a_group.id, b_group.id, *ids


@asynccontextmanager
async def open_world(kind: str, database_url: str) -> AsyncIterator[World]:
    """Open the ``sql`` or ``fake`` backend and seed it; the backend is closed on exit."""
    async with open_backend(kind, database_url) as backend:
        uow = backend.uow
        ids = await _seed(uow)
        await backend.forget()  # the legacy rows of the seeded groups: step 2e has its own tests
        engine = backend.engine
        if engine is None:

            async def archive_in_fake(pool_id: int) -> None:
                uow.state.pools[pool_id] = replace(uow.state.pools[pool_id], archived_at=NOW)

            async def forget_in_fake(pool_id: int) -> None:
                uow.state.pools[pool_id] = replace(uow.state.pools[pool_id], code=None)

            yield World(uow, *ids, None, archive_in_fake, forget_in_fake)
            return

        async def granted_by_sql(staff_id: int, skill_id: int) -> int | None:
            async with AsyncSession(engine) as session:
                return await session.scalar(
                    select(StaffSkillRow.granted_by).where(
                        StaffSkillRow.staff_id == staff_id, StaffSkillRow.skill_id == skill_id
                    )
                )

        async def pool_update(pool_id: int, **values: Any) -> None:
            async with AsyncSession(engine) as session, session.begin():
                await session.execute(
                    update(CapacityPoolRow).where(CapacityPoolRow.id == pool_id).values(**values)
                )

        async def archive_in_sql(pool_id: int) -> None:
            await pool_update(pool_id, archived_at=NOW)

        async def forget_in_sql(pool_id: int) -> None:
            await pool_update(pool_id, code=None)

        yield World(uow, *ids, granted_by_sql, archive_in_sql, forget_in_sql)


def refused(code: ErrorCode, field: str | None = None) -> Any:
    """Return a context manager asserting a domain error with this code and field."""
    return _Refusal(code, field)


class _Refusal:
    """Context manager asserting a ``DomainError`` of a given code and field."""

    def __init__(self, code: ErrorCode, field: str | None) -> None:
        self.code, self.field = code, field

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: Any, error: Any, trace: Any) -> bool:
        assert isinstance(error, DomainError), f"no DomainError, got {error!r}"
        assert (error.code, error.field) == (self.code, self.field)
        return True


async def add_pool(world: World, name: str = "Loket", skills: frozenset[int] = frozenset()) -> Any:
    """Store a counted pool of Acme and return it."""
    async with world.uow.locked() as tx:
        return await tx.save_capacity_pool(
            EVERYTHING,
            CapacityPool(None, world.acme, name, PoolMode.COUNTED, skills, code=name.lower()),
        )
