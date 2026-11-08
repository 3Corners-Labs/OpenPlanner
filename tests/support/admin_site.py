"""Helpers on a running admin site of the HTTP tests: a form post, its database and audit log.

The site is ``Site`` of ``tests/admin/conftest.py``: the app in-process with the demo data.
"""

from typing import Any

import httpx

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import Outcome
from app.domain.authorization import Scope
from tests.admin.conftest import SAME_ORIGIN, Site


def site_app(site: Site) -> Any:
    """Return the application the site runs."""
    return site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001


def site_uow(site: Site) -> SqlUnitOfWork:
    """Return a unit of work on the site's database."""
    return SqlUnitOfWork(site_app(site).state.engine)


async def post_form(site: Site, path: str, data: dict[str, str] | None = None) -> httpx.Response:
    """Post ``data`` (nothing if left out) to ``path`` as a form of the portal itself."""
    return await site.client.post(path, data=data or {}, headers=SAME_ORIGIN)


async def audit_events(site: Site) -> list[tuple[str, str, Outcome, str]]:
    """Return actor, action, outcome and reason of the newest 50 audit events, oldest first."""
    async with site_uow(site).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=50)
    return [
        (e.event.actor, e.event.action, e.event.outcome, e.event.reason) for e in reversed(events)
    ]
