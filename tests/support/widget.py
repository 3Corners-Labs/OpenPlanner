"""The widget API in HTTP tests (ADR 0035): the demo group embedded on the organisation's site."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import GroupRow, OrganisationOriginRow, OrganisationRow
from app.config import Settings
from tests.support.participant import FRIDAY_0900

SITE = "https://www.acme.example"
"""The organisation's website: the origin that ``embed`` allows."""
BASE = "/api/v1/widget/demo/demo"
"""The widget API of group demo of organisation demo."""
ADA = {
    "start": FRIDAY_0900,
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.org",
}
"""A booking of Friday 09:00 for Ada, as the widget posts it."""


async def embed(settings: Settings, *, enabled: bool = True) -> None:
    """Put ``SITE`` on organisation demo's list and switch embedding of group demo on or off."""
    engine = create_engine(settings.database_url)
    async with AsyncSession(engine) as session, session.begin():
        demo = await session.scalar(
            select(OrganisationRow.id).where(OrganisationRow.slug == "demo")
        )
        await session.execute(
            update(GroupRow).where(GroupRow.slug == "demo").values(embedding_enabled=enabled)
        )
        session.add(OrganisationOriginRow(organisation_id=demo, origin=SITE, position=0))
    await engine.dispose()
