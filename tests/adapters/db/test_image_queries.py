"""Closure images in SQLite (ADR 0037 §3): one per closure, replaced and removed cleanly."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.closure import Closure
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.images import ImageFile, ImageText
from app.domain.services import BookingService
from tests.adapters.db.conftest import count_images, seed_group
from tests.fakes.clock import FixedClock
from tests.support.closures import NOTICES
from tests.support.domain import NOW, TEST_TOKEN_KEY
from tests.support.images import jpeg, png
from tests.support.principals import principal

ADMIN = principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
TEXT = ImageText({"nl": "Gesloten deur", "en": "Closed door"})


async def _closure(engine: AsyncEngine) -> tuple[SqlUnitOfWork, Closure]:
    await seed_group(engine, slug="aangifte")
    uow = SqlUnitOfWork(engine)
    clock = FixedClock(NOW)
    bookings = BookingService(uow, clock, TEST_TOKEN_KEY)
    closures = ClosureService(uow, clock, AdminService(uow, clock), bookings)
    ends = NOW + timedelta(days=1)
    await closures.close_group(ADMIN, "aangifte", ClosureInput(None, ends, NOTICES))
    async with uow.read() as tx:
        group = await tx.get_group("aangifte")
        assert group is not None
        return uow, (await tx.get_closures(group.id, NOW))[0]


async def test_an_image_round_trips_with_its_text(engine: AsyncEngine) -> None:
    uow, closure = await _closure(engine)
    closure_id = closure.id or 0
    image = ImageFile.from_upload(png())

    async with uow.locked() as tx:
        assert await tx.get_closure_image(closure_id) is None
        await tx.save_closure_image(closure_id, TEXT, image)
    async with uow.read() as tx:
        stored = await tx.get_closure(closure_id)
        data = await tx.get_closure_image(closure_id)

    assert stored is not None
    assert stored.image == image.ref(TEXT)
    assert data == image


async def test_replacing_keeps_one_row_and_a_text_change_keeps_the_file(
    engine: AsyncEngine,
) -> None:
    uow, closure = await _closure(engine)
    closure_id = closure.id or 0
    first, second = ImageFile.from_upload(png()), ImageFile.from_upload(jpeg())
    decorative = ImageText(decorative=True)

    async with uow.locked() as tx:
        await tx.save_closure_image(closure_id, TEXT, first)
        await tx.save_closure_image(closure_id, TEXT, second)
        await tx.save_closure_image(closure_id, decorative, None)
    async with uow.read() as tx:
        stored = await tx.get_closure(closure_id)
        data = await tx.get_closure_image(closure_id)

    assert await count_images(engine) == 1
    assert stored is not None
    assert stored.image == second.ref(decorative)
    assert data == second


async def test_saving_the_closure_keeps_its_image_and_removing_deletes_the_row(
    engine: AsyncEngine,
) -> None:
    uow, closure = await _closure(engine)
    closure_id = closure.id or 0
    image = ImageFile.from_upload(png())
    async with uow.locked() as tx:
        await tx.save_closure_image(closure_id, TEXT, image)
        await tx.save_closure(closure)  # still without an image in memory
    async with uow.read() as tx:
        kept = await tx.get_closure(closure_id)
    assert kept is not None
    assert kept.image == image.ref(TEXT)

    async with uow.locked() as tx:
        await tx.remove_closure_image(closure_id)
        await tx.remove_closure_image(closure_id)  # nothing left: no error
    async with uow.read() as tx:
        removed = await tx.get_closure(closure_id)
        data = await tx.get_closure_image(closure_id)

    assert removed is not None
    assert removed.image is None
    assert data is None
    assert await count_images(engine) == 0
