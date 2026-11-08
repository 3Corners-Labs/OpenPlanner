"""What a purge leaves on the booking row and its timeline (ADR 0102 points 11 and 13)."""

import inspect
import re

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import BookingEventRow, BookingRow
from app.adapters.db.uow import SqlTransaction, SqlUnitOfWork
from app.domain.booking import MeetingRef
from app.domain.calls import BookingEvent, EventKind
from app.domain.contact_center import ContactCenterKind
from app.domain.privacy import PURGED_FIELDS
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, TEST_TOKEN_KEY, book


@pytest.fixture
def service(engine: AsyncEngine) -> BookingService:
    return BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY)


async def _booking_with_everything(engine: AsyncEngine, service: BookingService) -> int:
    """Book, then give the booking a meeting, a trial room, a work item and an event text."""
    await seed_group(engine)
    receipt = await book(service)
    booking_id = receipt.booking.id or 0
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_meeting(booking_id, MeetingRef("room-1", "https://example.test/join"))
        await tx.save_task_id(booking_id, "task-1", ContactCenterKind.WEBEX_CC)
        await tx.add_booking_event(
            BookingEvent(booking_id, NOW, EventKind.MEETING_FAILED, "rejected_400", "ada@x.test")
        )
    async with engine.begin() as conn:
        await conn.execute(
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(trial_room_id="trial-1", claimed_by="agent-1")
        )
    return booking_id


async def test_the_purge_clears_the_provider_links_and_the_event_text(
    engine: AsyncEngine, service: BookingService
) -> None:
    booking_id = await _booking_with_everything(engine, service)
    columns = [getattr(BookingRow, name) for name in PURGED_FIELDS]
    token = select(BookingRow.management_token_hash).where(BookingRow.id == booking_id)
    async with engine.connect() as conn:
        before = (await conn.execute(token)).scalar_one()

    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.purge_personal_data(booking_id, NOW)

    async with engine.connect() as conn:
        cleared = (await conn.execute(select(*columns).where(BookingRow.id == booking_id))).one()
        after = (await conn.execute(token)).scalar_one()
        salt = (
            await conn.execute(
                select(BookingRow.management_token_salt).where(BookingRow.id == booking_id)
            )
        ).scalar_one()
        messages = (
            (
                await conn.execute(
                    select(BookingEventRow.message).where(BookingEventRow.booking_id == booking_id)
                )
            )
            .scalars()
            .all()
        )
    assert tuple(cleared) == (None,) * len(PURGED_FIELDS)
    assert after != before  # the old hash can no longer find the booking
    assert re.fullmatch(r"[0-9a-f]{64}", after)
    assert salt != ""  # the salt stays (ADR 0102 point 11)
    assert messages
    assert set(messages) == {""}


async def test_the_purge_leaves_other_bookings_alone(
    engine: AsyncEngine, service: BookingService
) -> None:
    booking_id = await _booking_with_everything(engine, service)
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.purge_personal_data(booking_id + 1000, NOW)
    async with engine.connect() as conn:
        room = (
            await conn.execute(select(BookingRow.video_room_id).where(BookingRow.id == booking_id))
        ).scalar_one()
    assert room == "room-1"


def test_purge_personal_data_names_the_fields_of_privacy_and_no_others() -> None:
    """The column names in the update come from ``PURGED_FIELDS``, not from a second list."""
    source = inspect.getsource(SqlTransaction.purge_personal_data)
    assert "PURGED_FIELDS" in source
    assert not re.search(
        r"\b(first_name|email|video_room_id|contact_center_task_id|wxcc_task_id)\s*=", source
    )
