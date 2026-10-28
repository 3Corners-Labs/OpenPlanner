"""The retention overview page: whether personal data is removed on time (ADR 0037).

Per group in scope: how many bookings still hold personal data, how many of them are past
their purge moment, how many purges failed, and when the last purge ran. Counts and times
only, so the auditor role (which never sees personal data) can use it. The status is always
written out next to its colour (WCAG 1.4.1).

    GET /admin/retention     retention.view
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.admin.authz import AdminContext, RequirePermission, admin_service
from app.admin.pages import Locale, admin_moment
from app.admin.pages import page as _page
from app.domain.authorization import Permission

router = APIRouter(prefix="/admin", include_in_schema=False)


@dataclass(frozen=True)
class GroupRow:
    """One group as the table shows it."""

    name: str
    organisation: str
    holding: int
    overdue: int
    failed: int
    last_purged: str
    attention: bool


@router.get("/retention", response_class=HTMLResponse)
async def retention(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.RETENTION_VIEW))],
) -> HTMLResponse:
    """Show per group whether personal data is removed on time."""
    _ = locale.gettext
    overview = await admin_service(request).retention(context.principal)
    never = _("admin.retention.never")
    rows = [
        GroupRow(
            g.group.group.name,
            g.group.organisation_name,
            g.holding,
            g.overdue,
            g.failed_purges,
            admin_moment(request, locale, g.last_purged_at) if g.last_purged_at else never,
            g.needs_attention,
        )
        for g in overview.groups
    ]
    last = overview.last_purged_at
    return _page(
        request,
        locale,
        "admin/retention.html",
        context.principal,
        {
            "overview": overview,
            "rows": rows,
            "checked_at": admin_moment(request, locale, overview.checked_at),
            "last_purged": admin_moment(request, locale, last) if last else never,
        },
    )
