"""What a purge leaves on the booking row and its timeline (ADR 0102 points 11 and 13)."""

import inspect
import re

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import BookingEventRow, BookingRow, JobRow
from app.adapters.db.uow import SqlTransaction, SqlUnitOfWork
from app.domain.booking import MeetingRef
from app.domain.calls import BookingEvent, EventKind
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef
from app.domain.job_plan import JobType, PlannedJob
from app.domain.privacy import PURGED_FIELDS
from app.domain.purge import purge_booking
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, TEST_TOKEN_KEY, book
from tests.support.seed_rows import seed_group


@pytest.fixture
def service(engine: AsyncEngine) -> BookingService:
    return BookingService(
        SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0
    )


async def _booking_with_everything(engine: AsyncEngine, service: BookingService) -> int:
    """Book, then give the booking a meeting, a trial room, a work item and an event text."""
    await seed_group(engine)
    receipt = await book(service)
    booking_id = receipt.booking.id or 0
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_meeting(booking_id, MeetingRef("room-1", "https://example.test/join"))
        await tx.save_task(booking_id, ContactCenterTaskRef("task-1", ContactCenterKind.WEBEX_CC))
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


async def _renames(engine: AsyncEngine, booking_id: int) -> list[tuple[str | None, object]]:
    """The booking's ``rename_guest`` jobs as (channel, pinned start)."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(JobRow.channel, JobRow.pinned_start).where(
                JobRow.booking_id == booking_id, JobRow.type == JobType.RENAME_GUEST.value
            )
        )
        return [tuple(row) for row in rows]  # type: ignore[misc]


async def test_a_purge_enqueues_one_rename_for_a_booking_with_a_minted_guest(
    engine: AsyncEngine, service: BookingService
) -> None:
    booking_id = await _booking_with_everything(engine, service)
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.mark_guest_minted(booking_id, NOW)
        await tx.mark_guest_minted(booking_id, NOW.replace(hour=9))  # the first moment stays
    async with engine.connect() as conn:
        minted = (
            await conn.execute(
                select(BookingRow.guest_minted_at).where(BookingRow.id == booking_id)
            )
        ).scalar_one()
    assert minted == NOW

    for _ in range(2):
        async with SqlUnitOfWork(engine).locked() as tx:
            await purge_booking(tx, booking_id, NOW)

    assert await _renames(engine, booking_id) == [(None, None)]
    # The mark is no personal data and survives the purge: it is not a purged field.
    async with engine.connect() as conn:
        kept = (
            await conn.execute(
                select(BookingRow.guest_minted_at).where(BookingRow.id == booking_id)
            )
        ).scalar_one()
    assert kept == NOW
    assert "guest_minted_at" not in PURGED_FIELDS


async def test_a_purge_without_a_minted_guest_enqueues_no_rename(
    engine: AsyncEngine, service: BookingService
) -> None:
    booking_id = await _booking_with_everything(engine, service)

    async with SqlUnitOfWork(engine).locked() as tx:
        await purge_booking(tx, booking_id, NOW)

    assert await _renames(engine, booking_id) == []


async def test_the_adapters_purge_is_storage_only_and_adds_no_job(
    engine: AsyncEngine, service: BookingService
) -> None:
    booking_id = await _booking_with_everything(engine, service)
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.mark_guest_minted(booking_id, NOW)
        await tx.purge_personal_data(booking_id, NOW)

    assert await _renames(engine, booking_id) == []


async def test_replace_jobs_never_deletes_the_job_that_outlives_a_purge(
    engine: AsyncEngine, service: BookingService
) -> None:
    booking_id = await _booking_with_everything(engine, service)
    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.mark_guest_minted(booking_id, NOW)
        await purge_booking(tx, booking_id, NOW)
        await tx.replace_jobs(booking_id, [PlannedJob(JobType.SEND_CANCELLATION, NOW, None)])

    assert await _renames(engine, booking_id) == [(None, None)]
