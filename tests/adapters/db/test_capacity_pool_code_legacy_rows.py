"""A counted pool row without a code is read with the code its name gives (ADR 0121 step 2f).

The image from before migration 0110 does not write the column. It has no pool screen, so such a
row should not exist, but the reader must not fail on one: it derives the code the migration
would have given and writes nothing.
"""

from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import CapacityPoolRow, OrganisationRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.errors import DomainError, ErrorCode

EVERYTHING = Scope(everything=True)


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    created = create_engine(database_url, busy_timeout_ms=200)
    yield created
    await created.dispose()


async def test_a_counted_row_without_a_code_reads_with_the_derived_code(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine)
    async with AsyncSession(engine) as session, session.begin():
        organisation = OrganisationRow(name="Acme", slug="acme")
        session.add(organisation)
        await session.flush()
        organisation_id = organisation.id
    async with uow.locked() as tx:
        pool = await tx.save_capacity_pool(
            EVERYTHING, CapacityPool(None, organisation_id, "Één loket", PoolMode.COUNTED, code="x")
        )
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(update(CapacityPoolRow).values(code=None))

    async with uow.read() as tx:
        found = await tx.get_capacity_pool(EVERYTHING, pool.id or 0)
        listed = await tx.list_capacity_pools(EVERYTHING, organisation_id)
    assert found is not None
    assert found.code == "n-loket"
    assert [p.code for p in listed] == ["n-loket"]

    async with uow.locked() as tx:  # editing it keeps the derived code and writes nothing
        await tx.save_capacity_pool(EVERYTHING, replace(found, name="Ander loket"))
        with pytest.raises(DomainError) as changed:
            await tx.save_capacity_pool(EVERYTHING, replace(found, code="x"))
    assert (changed.value.code, changed.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "pool_code",
    )
    async with AsyncSession(engine) as session:
        assert (await session.scalars(select(CapacityPoolRow.code))).all() == [None]
