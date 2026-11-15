"""What System status counts during a key rotation (ADR 0094): numbers only."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.credentials import CredentialStore
from app.adapters.db.models import BookingRow, SlotRow
from app.adapters.db.rotation import open_booking_credentials, records_not_current
from app.domain.tokens import derive_token, hash_token
from tests.support.domain import NOW
from tests.support.seed_rows import seed_group

OLD = b"o" * 32
NEW = b"n" * 32


async def test_records_under_the_previous_key_are_counted_until_they_are_resealed(
    engine: AsyncEngine,
) -> None:
    await CredentialStore(engine, SecretBox(OLD)).put("webex_service_app", {"a": "b"}, NOW)
    after = SecretBox(NEW, [OLD])

    assert await records_not_current(engine, after) == 1

    await CredentialStore(engine, after).put("webex_service_app", {"a": "b"}, NOW)
    assert await records_not_current(engine, after) == 0


async def test_only_open_bookings_report_their_link(engine: AsyncEngine) -> None:
    group = await seed_group(engine)
    hour = timedelta(hours=1)
    async with AsyncSession(engine) as session, session.begin():
        future = SlotRow(
            group_id=group,
            start=NOW + timedelta(days=1),
            end=NOW + timedelta(days=1) + hour,
            capacity_snapshot=1,
        )
        past = SlotRow(
            group_id=group,
            start=NOW - timedelta(days=2),
            end=NOW - timedelta(days=2) + hour,
            capacity_snapshot=1,
        )
        session.add_all([future, past])
        await session.flush()
        for slot, status, salt in (
            (future, "confirmed", "open"),
            (future, "cancelled", "cancelled"),
            (past, "confirmed", "over"),
        ):
            session.add(
                BookingRow(
                    slot_id=slot.id,
                    group_id=group,
                    status=status,
                    locale="nl",
                    management_token_hash=hash_token(derive_token(OLD, salt)),
                    management_token_salt=salt,
                    public_ref=f"ref-{salt}",
                    created_at=NOW,
                )
            )

    credentials = await open_booking_credentials(engine, NOW)

    assert [c.salt for c in credentials] == ["open"]


async def test_held_bookings_report_their_link_after_their_slot_has_passed(
    engine: AsyncEngine,
) -> None:
    # Release audit v0.4.0 P2 (#508): a booking on hold under a closure and one awaiting a new
    # time keep receiving messages with a link after the original slot is over (ADR 0024, 0044).
    group = await seed_group(engine)
    async with AsyncSession(engine) as session, session.begin():
        past = SlotRow(
            group_id=group,
            start=NOW - timedelta(days=2),
            end=NOW - timedelta(days=2) + timedelta(hours=1),
            capacity_snapshot=3,
        )
        session.add(past)
        await session.flush()
        for status, salt in (
            ("suspended", "held"),
            ("awaiting_rebook", "rebook"),
            ("confirmed", "over"),
            ("cancelled", "cancelled"),
        ):
            session.add(
                BookingRow(
                    slot_id=past.id,
                    group_id=group,
                    status=status,
                    locale="nl",
                    management_token_hash=hash_token(derive_token(OLD, salt)),
                    management_token_salt=salt,
                    public_ref=f"ref-{salt}",
                    created_at=NOW,
                )
            )

    credentials = await open_booking_credentials(engine, NOW)

    assert sorted(c.salt for c in credentials) == ["held", "rebook"]
