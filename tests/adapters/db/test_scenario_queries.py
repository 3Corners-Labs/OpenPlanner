"""Closure scenarios in SQLite (ADR 0037 §4): per organisation or group, with an image."""

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.authorization import Principal, Role
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.domain.images import ImageFile, ImageText
from app.domain.scenarios import ClosureScenario
from app.domain.services import BookingService
from tests.adapters.db.conftest import count_images, seed_group
from tests.fakes.clock import FixedClock
from tests.support.closures import NOTICES
from tests.support.domain import NOW, TEST_TOKEN_KEY
from tests.support.images import png

ADMIN = Principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
BLOCK = ContactBlock((ContactOption.create(ContactKind.PHONE, "14 020"),), {"nl": "Bel ons"})
TEXT = ImageText({"nl": "Deur", "en": "Door"})


async def test_scenarios_round_trip_per_owner(engine: AsyncEngine) -> None:
    await seed_group(engine, slug="aangifte")
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        group = await tx.get_group("aangifte")
        assert group is not None
        org = group.organisation_id
        shared = await tx.add_scenario(
            ClosureScenario(None, org, None, "Storing", NOTICES, {"nl": "Storing"}, BLOCK)
        )
        own = await tx.add_scenario(ClosureScenario(None, org, group.id, "Gebouw dicht", NOTICES))
        await tx.save_scenario(ClosureScenario(own.id, org, group.id, "Brand", NOTICES))

    async with uow.read() as tx:
        organisation = await tx.list_scenarios(org, None)
        groups = await tx.list_scenarios(org, group.id)
        other = await tx.list_scenarios(org + 1, None)
        stored = await tx.get_scenario(shared.id or 0)
        missing = await tx.get_scenario(999)

    assert [s.name for s in organisation] == ["Storing"]
    assert [s.name for s in groups] == ["Brand"]
    assert other == []
    assert stored is not None
    assert (dict(stored.notices), dict(stored.headings), stored.contacts) == (
        NOTICES,
        {"nl": "Storing"},
        BLOCK,
    )
    assert missing is None


async def test_a_scenario_image_is_kept_on_save_copied_on_start_and_deleted_with_it(
    engine: AsyncEngine,
) -> None:
    await seed_group(engine, slug="aangifte")
    uow = SqlUnitOfWork(engine)
    image = ImageFile.from_upload(png())
    async with uow.locked() as tx:
        group = await tx.get_group("aangifte")
        assert group is not None
        scenario = await tx.add_scenario(
            ClosureScenario(None, group.organisation_id, None, "Storing", NOTICES)
        )
        scenario_id = scenario.id or 0
        await tx.save_scenario_image(scenario_id, TEXT, image)
        await tx.save_scenario(scenario)  # still without the image in memory
    async with uow.read() as tx:
        kept = await tx.get_scenario(scenario_id)
        assert kept is not None
        assert kept.image == image.ref(TEXT)
        assert await tx.get_scenario_image(scenario_id) == image

    clock = FixedClock(NOW)
    closures = ClosureService(
        uow, clock, AdminService(uow, clock), BookingService(uow, clock, TEST_TOKEN_KEY)
    )
    start = ClosureInput(None, None, NOTICES, scenario_id=scenario_id)
    await closures.close_group(ADMIN, "aangifte", start)
    assert await count_images(engine) == 2  # the closure has its own copy

    async with uow.locked() as tx:
        await tx.delete_scenario(scenario_id)
    async with uow.read() as tx:
        closure = (await tx.get_closures(group.id, NOW))[0]
        assert await tx.get_scenario(scenario_id) is None
        assert await tx.get_closure_image(closure.id or 0) == image
    assert closure.image == image.ref(TEXT)
    assert await count_images(engine) == 1

    async with uow.locked() as tx:
        again = await tx.add_scenario(
            ClosureScenario(None, group.organisation_id, None, "Staking", NOTICES)
        )
        await tx.save_scenario_image(again.id or 0, TEXT, image)
        await tx.remove_scenario_image(again.id or 0)
        await tx.remove_scenario_image(again.id or 0)
        assert await tx.get_scenario_image(again.id or 0) is None
    assert await count_images(engine) == 1
