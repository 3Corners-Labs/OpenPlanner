"""``save_contact``: a correction writes the contact columns only (ADR 0056 point 10)."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import BookingRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import Contact
from app.domain.scheduling import PhoneMode
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, TEST_TOKEN_KEY, book
from tests.support.seed_rows import seed_group


async def test_a_correction_keeps_the_email_status_and_routing(engine: AsyncEngine) -> None:
    await seed_group(engine)
    receipt = await book(
        BookingService(SqlUnitOfWork(engine), FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0)
    )
    booking_id = receipt.booking.id or 0
    async with engine.begin() as conn:
        await conn.execute(
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(subject_code="move", routing_answers='{"preferred_language": "en"}')
        )
    corrected = Contact.create(
        first_name="Augusta",
        last_name="Byron",
        email="ada@example.org",
        phone="06 8765 4321",
        phone_mode=PhoneMode.OPTIONAL,
        region="NL",
    )

    async with SqlUnitOfWork(engine).locked() as tx:
        await tx.save_contact(booking_id, corrected)

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    BookingRow.first_name,
                    BookingRow.last_name,
                    BookingRow.email,
                    BookingRow.phone,
                    BookingRow.status,
                    BookingRow.subject_code,
                    BookingRow.routing_answers,
                ).where(BookingRow.id == booking_id)
            )
        ).one()
    assert tuple(row) == (
        "Augusta",
        "Byron",
        "ada@example.org",
        "+31687654321",
        "confirmed",
        "move",
        '{"preferred_language": "en"}',
    )
