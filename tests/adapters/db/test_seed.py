"""Closure examples of the demo seed (ADR 0037): fixtures of the citizen pages and e2e."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import (
    CLOSED_GROUP_SLUG,
    PLANNED_GROUP_SLUG,
    seed_closure_examples,
    seed_demo,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import BookingStatus
from app.domain.tokens import hash_token
from tests.support.domain import NOW

KEY = b"k" * 32


async def test_a_closed_team_a_planned_closure_and_a_suspended_booking(
    engine: AsyncEngine,
) -> None:
    await seed_demo(engine)

    examples = await seed_closure_examples(engine, NOW, KEY)

    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        closed = await tx.get_group(CLOSED_GROUP_SLUG)
        planned = await tx.get_group(PLANNED_GROUP_SLUG)
        assert closed is not None
        assert planned is not None
        (active,) = await tx.get_closures(closed.id, NOW)
        (upcoming,) = await tx.get_closures(planned.id, NOW)
        token = examples.suspended_booking.removeprefix("/b/")
        booking = await tx.get_booking_by_token_hash(hash_token(token))
        pending = [
            await tx.get_booking_by_token_hash(
                hash_token(path.removeprefix("/b/").removesuffix("/confirm"))
            )
            for path in (
                examples.pending_confirm,
                examples.pending_flow_chromium,
                examples.pending_flow_firefox,
                examples.pending_flow_webkit,
            )
        ]
    assert active.is_active(NOW)
    assert active.heading("en")
    assert active.contacts is not None
    assert active.image is not None  # the closure page with an image (CL-3)
    assert active.image.text.alt("en")
    assert upcoming.image is None  # and one without
    assert not upcoming.is_active(NOW)
    assert upcoming.is_visible(NOW)
    assert upcoming.starts_at - NOW > timedelta(days=1)
    assert booking is not None
    assert booking.status is BookingStatus.SUSPENDED
    assert booking.closure_id == active.id
    assert [b.status if b else None for b in pending] == [BookingStatus.PENDING_CONFIRMATION] * 4
    assert len({b.start for b in pending if b}) == 4  # one per slot
    assert examples.closed_group == f"/g/{CLOSED_GROUP_SLUG}"
    assert examples.planned_closure == f"/g/{PLANNED_GROUP_SLUG}/closure"


async def test_a_second_run_adds_only_a_new_booking_link(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    first = await seed_closure_examples(engine, NOW, KEY)

    second = await seed_closure_examples(engine, NOW, KEY)

    async with SqlUnitOfWork(engine).read() as tx:
        closed = await tx.get_group(CLOSED_GROUP_SLUG)
        assert closed is not None
        closures = await tx.get_closures(closed.id, NOW)
    assert len(closures) == 1
    assert second.suspended_booking != first.suspended_booking
    assert first.trial_page == f"{first.suspended_booking}/test"
