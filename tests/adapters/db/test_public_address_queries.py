"""Storage of the installation id, the pending probe nonce and the latest round trips (ADR 0086)."""

from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import InstanceRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.public_address import AddressCheck, AddressFinding, AddressKind, Forwarded
from tests.support.domain import NOW


def check(kind: AddressKind = AddressKind.PUBLIC, **update: object) -> AddressCheck:
    base = AddressCheck(kind, NOW, "https://afspraak.example.nl", AddressFinding.OK, "https")
    return AddressCheck(**{**base.__dict__, **update})  # type: ignore[arg-type]


async def test_the_instance_id_is_made_once_and_never_changes(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        assert await tx.get_instance_id() is None
    async with uow.locked() as tx:
        first = await tx.ensure_instance_id()
    async with uow.locked() as tx:
        second = await tx.ensure_instance_id()

    assert first == second
    assert len(first) == 36
    async with uow.read() as tx:
        assert await tx.get_instance_id() == first


async def test_a_nonce_is_stored_as_given_and_consumed_once(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.store_probe_nonce("a" * 64, NOW)
    async with uow.locked() as tx:
        wrong = await tx.take_probe_nonce("b" * 64, NOW - timedelta(minutes=5))
        taken = await tx.take_probe_nonce("a" * 64, NOW - timedelta(minutes=5))
    async with uow.locked() as tx:
        again = await tx.take_probe_nonce("a" * 64, NOW - timedelta(minutes=5))

    assert (wrong, taken, again) == (False, True, False)


async def test_a_new_nonce_replaces_the_pending_one_and_an_old_one_is_refused(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.store_probe_nonce("a" * 64, NOW)
        await tx.store_probe_nonce("b" * 64, NOW - timedelta(minutes=10))
    async with uow.locked() as tx:
        replaced = await tx.take_probe_nonce("a" * 64, NOW - timedelta(minutes=5))
        too_old = await tx.take_probe_nonce("b" * 64, NOW - timedelta(minutes=5))

    assert (replaced, too_old) == (False, False)


async def test_the_latest_round_trip_per_address_is_replaced_and_can_be_forgotten(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_address_check(check())
        await tx.save_address_check(check(AddressKind.WEBHOOK, finding=AddressFinding.REDIRECTED))
        await tx.save_address_check(
            check(finding=AddressFinding.UNREACHABLE, scheme=None, error="timeout")
        )
    async with uow.read() as tx:
        stored = {c.kind: c for c in await tx.get_address_checks()}

    assert stored[AddressKind.PUBLIC].finding is AddressFinding.UNREACHABLE
    assert stored[AddressKind.PUBLIC].error == "timeout"
    assert stored[AddressKind.WEBHOOK].finding is AddressFinding.REDIRECTED
    async with uow.locked() as tx:
        await tx.delete_address_check(AddressKind.WEBHOOK)
    async with uow.read() as tx:
        assert [c.kind for c in await tx.get_address_checks()] == [AddressKind.PUBLIC]


async def test_the_forwarded_state_round_trips_and_a_hand_edited_row_does_not_break_the_page(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_address_check(check(forwarded=Forwarded.HONOURED))
    async with uow.read() as tx:
        assert (await tx.get_address_checks())[0].forwarded is Forwarded.HONOURED
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE public_address_checks SET finding = 'bogus', forwarded = 'bogus'")
        )
        await connection.execute(
            text(
                "INSERT INTO public_address_checks (kind, checked_at, url, finding, error) "
                "VALUES ('elsewhere', '2026-01-01 00:00:00', 'x', 'ok', '')"
            )
        )
    async with uow.read() as tx:
        rows = await tx.get_address_checks()

    assert [(c.kind, c.finding, c.forwarded) for c in rows] == [
        (AddressKind.PUBLIC, AddressFinding.UNREACHABLE, None)
    ]


async def test_only_a_hash_and_no_address_are_stored(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.store_probe_nonce("a" * 64, NOW)
    async with engine.connect() as connection:
        columns = [r[1] for r in await connection.execute(text("PRAGMA table_info(instance)"))]
        row = (await connection.execute(select(InstanceRow.probe_nonce_hash))).scalar_one()

    assert columns == ["id", "instance_id", "probe_nonce_hash", "probe_nonce_at"]
    assert row == "a" * 64
