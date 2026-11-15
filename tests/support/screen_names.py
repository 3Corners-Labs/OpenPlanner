"""Helpers of the screen-name tests on a running admin site (ADR 0123 steps 5c and 5d).

Both the manager's form on the staff page and the person's own form on *My profile* are tested
through the same few moves: let people log in (so each has a staff record), find a person's
number in the staff list and read the stored screen name. The audit rows of its event come
from ``tests.support.admin_site.audit_rows``.
"""

from sqlalchemy import text

from tests.support.admin_site import Site


async def id_of(site: Site, name: str) -> str:
    """Return the number of the person called ``name`` in the staff list the viewer sees."""
    page = (await site.client.get("/admin/staff")).text
    return page.split(f'">{name}</a>')[0].rsplit('href="/admin/staff/', 1)[1]


async def login_each(site: Site, *principals: object) -> None:
    """Let each person log in once (so they have a staff record); the last one stays."""
    for principal in principals:
        site.client.cookies.clear()
        await site.login_as(principal)  # type: ignore[arg-type]


async def stored(site: Site, subject: str) -> str | None:
    """Return the screen name kept for ``subject`` in the database."""
    app = site.app
    async with app.state.engine.connect() as conn:
        row = await conn.execute(
            text("select screen_name from staff where subject = :s"), {"s": subject}
        )
        return row.scalar_one()
