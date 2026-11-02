"""Participant link sessions in SQLite: the queries and the service on top (ADR 0093)."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import (
    BookingRow,
    ParticipantLinkBookingRow,
    ParticipantLinkSessionRow,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.participant_link_service import ParticipantLinkService
from app.domain.participant_links import MAX_BOOKINGS, hash_value
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, SLOT_0900, SLOT_0930, TEST_TOKEN_KEY, book

DAY = timedelta(hours=24)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def uow(engine: AsyncEngine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine, None)


@pytest.fixture
def links(uow: SqlUnitOfWork, clock: FixedClock) -> ParticipantLinkService:
    return ParticipantLinkService(uow, clock)


async def _bookings(
    engine: AsyncEngine, uow: SqlUnitOfWork, clock: FixedClock
) -> list[tuple[int, str]]:
    """Two bookings of one group: their ids and public references."""
    await seed_group(engine, slug="aangifte", capacity=2)
    service = BookingService(uow, clock, TEST_TOKEN_KEY)
    made = [(await book(service, start)).booking for start in (SLOT_0900, SLOT_0930)]
    return [(b.id or 0, b.public_ref) for b in made]


async def test_every_booking_gets_its_own_public_reference(
    engine: AsyncEngine, uow: SqlUnitOfWork, clock: FixedClock
) -> None:
    (first_id, first_ref), (_, second_ref) = await _bookings(engine, uow, clock)

    assert first_ref != second_ref
    assert len(first_ref) >= 22
    async with uow.read() as tx:
        assert await tx.get_booking_id_by_public_ref(first_ref) == first_id
        assert await tx.get_booking_id_by_public_ref("nope") is None
        by_ref = await tx.get_booking_by_public_ref(second_ref)
        assert by_ref is not None
        assert by_ref.public_ref == second_ref
        assert await tx.get_booking_by_public_ref("nope") is None


async def test_the_first_link_starts_a_session_and_keeps_only_a_hash(
    engine: AsyncEngine, uow: SqlUnitOfWork, links: ParticipantLinkService, clock: FixedClock
) -> None:
    (booking_id, ref), _ = await _bookings(engine, uow, clock)

    value = await links.admit(booking_id, None, DAY)

    assert await links.allows(value, ref, DAY)
    async with AsyncSession(engine) as session:
        stored = (await session.scalars(select(ParticipantLinkSessionRow))).one()
    assert stored.value_hash == hash_value(value)
    assert value not in repr(stored.__dict__)
    assert stored.expires_at == NOW + DAY


async def test_a_second_booking_joins_the_same_session(
    engine: AsyncEngine, uow: SqlUnitOfWork, links: ParticipantLinkService, clock: FixedClock
) -> None:
    (first_id, first_ref), (second_id, second_ref) = await _bookings(engine, uow, clock)
    value = await links.admit(first_id, None, DAY)

    again = await links.admit(second_id, value, DAY)
    same = await links.admit(second_id, value, DAY)  # a booking it holds is not added twice

    assert again == value == same
    assert await links.allows(value, first_ref, DAY)
    assert await links.allows(value, second_ref, DAY)
    async with AsyncSession(engine) as session:
        held = await session.scalar(select(func.count()).select_from(ParticipantLinkBookingRow))
        sessions = await session.scalar(select(func.count()).select_from(ParticipantLinkSessionRow))
    assert (held, sessions) == (2, 1)


async def test_a_session_holds_no_booking_it_was_not_given(
    engine: AsyncEngine, uow: SqlUnitOfWork, links: ParticipantLinkService, clock: FixedClock
) -> None:
    (first_id, _), (_, second_ref) = await _bookings(engine, uow, clock)
    value = await links.admit(first_id, None, DAY)

    assert not await links.allows(value, second_ref, DAY)
    assert not await links.allows(value, "unknown", DAY)
    assert not await links.allows(None, second_ref, DAY)
    assert not await links.allows("", second_ref, DAY)
    assert not await links.allows("forged", second_ref, DAY)


async def test_use_extends_the_session_and_idle_ends_it(
    engine: AsyncEngine, uow: SqlUnitOfWork, links: ParticipantLinkService, clock: FixedClock
) -> None:
    (booking_id, ref), _ = await _bookings(engine, uow, clock)
    value = await links.admit(booking_id, None, DAY)

    clock.advance(timedelta(hours=20))
    assert await links.allows(value, ref, DAY)  # extends to 44 hours after the start
    clock.advance(timedelta(hours=20))
    assert await links.allows(value, ref, DAY)
    clock.advance(timedelta(hours=24, seconds=1))

    assert not await links.allows(value, ref, DAY)


async def test_a_request_within_a_minute_does_not_write(
    engine: AsyncEngine, uow: SqlUnitOfWork, links: ParticipantLinkService, clock: FixedClock
) -> None:
    (booking_id, ref), _ = await _bookings(engine, uow, clock)
    value = await links.admit(booking_id, None, DAY)

    clock.advance(timedelta(seconds=30))
    await links.allows(value, ref, DAY)

    async with uow.read() as tx:
        session = await tx.get_link_session(hash_value(value))
    assert session is not None
    assert session.expires_at == NOW + DAY


async def test_a_lapsed_session_is_replaced_by_a_new_one(
    engine: AsyncEngine, uow: SqlUnitOfWork, links: ParticipantLinkService, clock: FixedClock
) -> None:
    (booking_id, ref), _ = await _bookings(engine, uow, clock)
    old = await links.admit(booking_id, None, DAY)
    clock.advance(DAY + timedelta(minutes=1))

    fresh = await links.admit(booking_id, old, DAY)

    assert fresh != old
    assert await links.allows(fresh, ref, DAY)
    assert not await links.allows(old, ref, DAY)
    async with AsyncSession(engine) as session:
        assert (
            await session.scalar(select(func.count()).select_from(ParticipantLinkSessionRow)) == 1
        )


async def _extra_bookings(engine: AsyncEngine, count: int) -> list[int]:
    """Insert ``count`` more bookings into the slot of an existing one; return their ids."""
    ids = list(range(100, 100 + count))
    async with AsyncSession(engine) as raw, raw.begin():
        template = (await raw.scalars(select(BookingRow))).first()
        assert template is not None
        for n in ids:
            raw.add(
                BookingRow(
                    id=n,
                    slot_id=template.slot_id,
                    group_id=template.group_id,
                    status="cancelled",
                    locale="nl",
                    management_token_hash=f"hash-{n}",
                    management_token_salt="s",
                    public_ref=f"ref-{n}",
                    created_at=NOW,
                )
            )
    return ids


async def test_past_twenty_bookings_the_oldest_drops_out(
    engine: AsyncEngine, uow: SqlUnitOfWork, links: ParticipantLinkService, clock: FixedClock
) -> None:
    (first_id, first_ref), _ = await _bookings(engine, uow, clock)
    ids = await _extra_bookings(engine, MAX_BOOKINGS)
    value = await links.admit(first_id, None, DAY)
    for n in ids[: MAX_BOOKINGS - 1]:
        clock.advance(timedelta(seconds=1))
        await links.admit(n, value, DAY)
    async with uow.read() as tx:
        full = await tx.get_link_session(hash_value(value))
    assert full is not None
    assert len(full.booking_ids) == MAX_BOOKINGS

    clock.advance(timedelta(seconds=1))
    await links.admit(ids[-1], value, DAY)

    async with uow.read() as tx:
        after = await tx.get_link_session(hash_value(value))
    assert after is not None
    assert len(after.booking_ids) == MAX_BOOKINGS
    assert first_id not in after.booking_ids  # the oldest went
    assert ids[-1] in after.booking_ids
    assert not await links.allows(value, first_ref, DAY)


async def test_a_purged_booking_leaves_every_session(
    engine: AsyncEngine, uow: SqlUnitOfWork, links: ParticipantLinkService, clock: FixedClock
) -> None:
    (first_id, first_ref), (second_id, second_ref) = await _bookings(engine, uow, clock)
    value = await links.admit(first_id, None, DAY)
    await links.admit(second_id, value, DAY)

    async with uow.locked() as tx:
        await tx.purge_personal_data(first_id, NOW + timedelta(days=3))

    assert not await links.allows(value, first_ref, DAY)
    assert await links.allows(value, second_ref, DAY)
    async with uow.read() as tx:
        # The booking row and its reference survive the purge (not PII).
        assert await tx.get_booking_id_by_public_ref(first_ref) == first_id


async def test_the_sweep_deletes_lapsed_sessions_and_their_lists(
    engine: AsyncEngine, uow: SqlUnitOfWork, links: ParticipantLinkService, clock: FixedClock
) -> None:
    (first_id, _), (second_id, second_ref) = await _bookings(engine, uow, clock)
    old = await links.admit(first_id, None, DAY)
    clock.advance(timedelta(hours=30))
    young = await links.admit(second_id, None, DAY)

    assert await links.clean() == 1

    async with AsyncSession(engine) as session:
        hashes = list(await session.scalars(select(ParticipantLinkSessionRow.value_hash)))
        held = list(await session.scalars(select(ParticipantLinkBookingRow.booking_id)))
    assert hashes == [hash_value(young)]
    assert held == [second_id]
    assert not await links.allows(old, "x", DAY)
    assert await links.allows(young, second_ref, DAY)
