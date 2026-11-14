"""Helpers of the screen-name tests on a running admin site (ADR 0123 steps 5c and 5d).

Both the manager's form on the staff page and the person's own form on *My profile* are tested
through the same few moves: let people log in (so each has a staff record), find a person's
number in the staff list, read the stored screen name and read the audit rows of its event.
"""

from sqlalchemy import text

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from tests.admin.conftest import Site


async def id_of(site: Site, name: str) -> str:
    """Return the number of the person called ``name`` in the staff list the viewer sees."""
    page = (await site.client.get("/admin/staff")).text
    return page.split(f'">{name}</a>')[0].rsplit('href="/admin/staff/', 1)[1]


async def login_each(site: Site, *principals: object) -> None:
    """Let each person log in once (so they have a staff record); the last one stays."""
    for principal in principals:
        site.client.cookies.clear()
        await site.login_as(principal)  # type: ignore[arg-type]


async def audit_rows(site: Site) -> list[tuple[str, str, str, str, str | None, str]]:
    """Return every audit event as (actor, action, outcome, reason, organisation, detail)."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        found = await tx.list_audit_events(Scope(everything=True), limit=300)
    return [
        (
            e.event.actor,
            e.event.action,
            e.event.outcome.value,
            e.event.reason,
            e.event.organisation,
            e.event.detail,
        )
        for e in found
    ]


async def stored(site: Site, subject: str) -> str | None:
    """Return the screen name kept for ``subject`` in the database."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with app.state.engine.connect() as conn:
        row = await conn.execute(
            text("select screen_name from staff where subject = :s"), {"s": subject}
        )
        return row.scalar_one()
