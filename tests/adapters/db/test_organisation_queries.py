"""Organisation storage: insert with default branding, update name and locale only."""

from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.branding import Branding
from app.domain.scheduling import Organisation


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
