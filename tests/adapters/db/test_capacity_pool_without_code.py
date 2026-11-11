"""A counted pool stored without a code is read, saved and archived as it is (ADR 0121 step 2f).

The image from before migration 0110 does not write the column, so such a row can exist. Its code
is never derived from its name (a rename would change it and two pools could show one): it reads
as ``None``, a name change keeps it ``None``, and it neither blocks nor is blocked by another
pool's code. One set of cases for the SQL adapter and the fake.
"""

from dataclasses import replace

from app.domain.capacity import CapacityPool, PoolMode
from app.domain.errors import ErrorCode
from tests.adapters.db import test_service_flow_contract as contract
from tests.adapters.db.test_service_flow_contract import EVERYTHING, LATER, World, add_pool, refused

world = contract.world  # the fixture of the shared cases: SQL adapter and fake


async def codeless(world: World, name: str = "Loket") -> CapacityPool:
    """Store a counted pool and clear its code, as a row from before the column has it."""
    pool = await add_pool(world, name)
    await world.forget_pool_code(pool.id)
    return pool


async def test_a_pool_without_a_code_lists_and_opens_with_none(world: World) -> None:
    pool = await codeless(world, "Eerste")

    async with world.uow.read() as tx:
        found = await tx.get_capacity_pool(EVERYTHING, pool.id)
        listed = await tx.list_capacity_pools(EVERYTHING, world.acme)
    assert found is not None
    assert found.code is None
    assert [(p.name, p.code) for p in listed] == [("Eerste", None)]


async def test_a_name_change_keeps_the_pool_without_a_code(world: World) -> None:
    pool = await codeless(world)

    async with world.uow.locked() as tx:
        saved = await tx.save_capacity_pool(
            EVERYTHING, replace(pool, name="Ander loket", code=None)
        )
        again = await tx.get_capacity_pool(EVERYTHING, pool.id)
    assert (saved.name, saved.code) == ("Ander loket", None)
    assert again is not None
    assert (again.name, again.code) == ("Ander loket", None)  # the name is not turned into a code


async def test_a_pool_without_a_code_can_be_archived(world: World) -> None:
    pool = await codeless(world)

    async with world.uow.locked() as tx:
        await tx.archive_capacity_pool(EVERYTHING, pool.id, LATER)
        archived = await tx.list_capacity_pools(EVERYTHING, world.acme, archived=True)
    assert [(p.id, p.code, p.archived_at) for p in archived] == [(pool.id, None, LATER)]


async def test_a_pool_without_a_code_neither_blocks_nor_is_blocked_by_a_code(
    world: World,
) -> None:
    pool = await codeless(world, "Loket")  # its name would derive to "loket"

    async with world.uow.locked() as tx:
        # Another pool may take the code the name would have given, and any other code.
        same = await tx.save_capacity_pool(
            EVERYTHING, CapacityPool(None, world.acme, "Nieuw", PoolMode.COUNTED, code="loket")
        )
        # Two pools without a code do not clash either (NULLs are distinct).
        second = await tx.save_capacity_pool(
            EVERYTHING, CapacityPool(None, world.acme, "Tweede", PoolMode.COUNTED, code="tweede")
        )
    await world.forget_pool_code(second.id)
    async with world.uow.locked() as tx:
        await tx.save_capacity_pool(EVERYTHING, replace(pool, name="Eerste", code=None))
        await tx.save_capacity_pool(EVERYTHING, replace(second, name="Tweede b", code=None))
        # The taken code is still refused for a new pool.
        with refused(ErrorCode.SLUG_TAKEN, "pool_code"):
            await tx.save_capacity_pool(
                EVERYTHING,
                CapacityPool(None, world.acme, "Derde", PoolMode.COUNTED, code=same.code),
            )
