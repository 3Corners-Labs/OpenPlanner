"""Organisation storage: insert with default branding, update name and locale only."""

from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.branding import Branding
from app.domain.scheduling import Group, Organisation


async def test_organisation_is_added_listed_and_updated(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        stored = await tx.add_organisation(Organisation(0, "Gemeente Noord", "noord", "en"))
    assert stored.id > 0
    assert (stored.name, stored.default_locale, stored.branding) == (
        "Gemeente Noord",
        "en",
        Branding(),
    )

    async with uow.locked() as tx:
        await tx.save_branding(stored.id, Branding("Noord", "#154273"))
        await tx.save_organisation(
            replace(stored, name="Noord", slug="ignored", default_locale="nl")
        )
    async with uow.read() as tx:
        found = await tx.get_organisation("noord")
        listed = await tx.list_organisations(Scope(everything=True))
    assert found is not None
    assert (found.name, found.slug, found.default_locale) == ("Noord", "noord", "nl")
    assert found.branding == Branding("Noord", "#154273")  # branding is left alone
    assert [o.slug for o in listed] == ["noord"]


async def test_an_empty_organisation_is_deleted_with_its_branding(engine: AsyncEngine) -> None:
    """ADR 0084 point 4: the cascades take the organisation's own rows; groups keep it."""
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        stored = await tx.add_organisation(Organisation(0, "Gemeente Noord", "noord", "en"))
        await tx.save_branding(stored.id, Branding("Noord", "#154273"))
        group = await tx.add_group(
            Group(0, stored.id, "Team", "noord-team", "Europe/Amsterdam", 30)
        )

    async with uow.read() as tx:
        with_group = await tx.count_groups(stored.id)
        clients = await tx.count_api_clients(stored.id)
    async with uow.locked() as tx:
        await tx.delete_group(group.id)
    async with uow.read() as tx:
        without_group = await tx.count_groups(stored.id)
    async with uow.locked() as tx:
        await tx.delete_organisation(stored.id)
    async with uow.read() as tx:
        gone = await tx.get_organisation("noord")
        logo = await tx.get_logo("noord")
        listed = [o.slug for o in await tx.list_organisations(Scope(everything=True))]

    assert (with_group, clients, without_group) == (1, 0, 0)
    assert gone is None
    assert logo is None
    assert "noord" not in listed
