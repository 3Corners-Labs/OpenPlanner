"""The overview as a grid of widgets, without script (ADR 0061 step 1, OV-1 and OV-2).

Every widget is a server-rendered fragment with its own route and its own permission, both
from the widget register ``app.web.widgets``. The page renders the visible widgets of the
admin's role default once at load, with a *Refresh* link; the fragment routes answer the same
HTML without the shell, for the live script of a later step (OV-5). A widget the admin may not
see is not rendered and its route refuses (the matrix, ADR 0014).

    GET  /admin/                              dashboard.view  the grid, then the group cards
    GET  /admin/overview/w/rights             rights.view     my role and reach
    GET  /admin/overview/w/bookings-today     bookings.view   confirmed bookings today, per group
    GET  /admin/overview/w/changes            audit.view      recent configuration changes

No PII in any fragment: counts, group names, times and admins' display names only (ADR 0061
§8). The widgets that need a data source this repository does not have yet (alerts, queue,
tasks, status) are declared in the register but not available, so they have no route here.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.admin.audit_routes import audit_views
from app.admin.authz import AdminContext, RequirePermission, admin_service, can_function
from app.admin.pages import Locale, page, role_labels, scope_text, templates
from app.admin.shortfalls import agenda_service, shortfall_view
from app.domain.admin import LOGIN_EVENT, LOGOUT_EVENT
from app.domain.audit import AuditEntry, AuditFilter, Category, Outcome, Source
from app.domain.authorization import (
    READ_PERMISSIONS,
    Permission,
    Principal,
    permissions_for,
)
from app.domain.staff import CREATED_EVENT
from app.web.authz_labels import PERMISSION_LABELS
from app.web.locale import RequestLocale
from app.web.templates import FragmentResponse, render
from app.web.widgets import Widget, visible, widget

router = APIRouter(prefix="/admin", include_in_schema=False)

FRAME = "admin/widgets/_frame.html"
"""The template that renders one widget: title, body, empty state and footer."""

NOT_CHANGES = frozenset(
    {p.value for p in READ_PERMISSIONS}
    | {Permission.AUDIT_EXPORT.value, LOGIN_EVENT, LOGOUT_EVENT, CREATED_EVENT}
)
"""Successful admin actions that changed no configuration: reads, the export (ADR 0040),
logins, logouts and the staff record a first login creates (ADR 0046)."""


@dataclass(frozen=True)
class WidgetView:
    """One rendered widget: its declaration and what its template needs.

    Attributes:
        widget: The register entry.
        rows: The rows to show; the body template knows their shape.
        total: How many rows there are in all; more than ``rows`` shows a "more" link.
        more_href: Where the whole list lives, if the admin may open it.
        extra: Further template context (the rights widget's role chips).
    """

    widget: Widget
    rows: list[object]
    total: int
    more_href: str | None = None
    extra: dict[str, object] | None = None


@dataclass(frozen=True)
class GroupCount:
    """Today's confirmed bookings of one group (counts only, ADR 0061 §8)."""

    name: str
    organisation: str
    count: int
    href: str


@dataclass(frozen=True)
class Change:
    """One configuration change as the widget lists it."""

    when: str
    actor: str
    action: str
    target: str
    href: str


Renderer = Callable[[Request, Locale, Principal], Awaitable[WidgetView]]


async def _rights(request: Request, locale: RequestLocale, principal: Principal) -> WidgetView:
    """My role and reach: the roles and the first permissions with their scope."""
    _ = locale.gettext
    rights = [
        (_(PERMISSION_LABELS[permission]), scope_text(scope, _))
        for permission, scope in permissions_for(principal)
    ]
    item = _declared("rights")
    return WidgetView(
        item,
        list(rights[: item.default_rows]),
        len(rights),
        "/admin/rights",
        {"roles": role_labels(principal, locale)},
    )


async def _bookings_today(
    request: Request, locale: RequestLocale, principal: Principal
) -> WidgetView:
    """Bookings today per group: a count per group whose bookings the admin may view."""
    service = admin_service(request)
    groups = await service.groups(principal, Permission.BOOKINGS_VIEW)
    counts = await agenda_service(request).counts_today(principal)
    rows = [
        GroupCount(
            g.group.name,
            g.organisation_name,
            counts.get(g.group.slug, 0),
            f"/admin/groups/{g.group.slug}/agenda",
        )
        for g in sorted(groups, key=lambda g: (-counts.get(g.group.slug, 0), g.group.name))
    ]
    item = _declared("bookings-today")
    return WidgetView(item, list(rows[: item.default_rows]), len(rows))


def _is_change(entry: AuditEntry) -> bool:
    """Whether an audit entry records a successful change of the configuration."""
    event = entry.event
    return event.outcome is Outcome.SUCCESS and event.action not in NOT_CHANGES


async def _changes(request: Request, locale: RequestLocale, principal: Principal) -> WidgetView:
    """Recent configuration changes: successful admin actions in the admin's audit scope."""
    item = _declared("changes")
    narrow = AuditFilter(source=Source.ADMIN, category=Category.AUDIT)
    entries = await admin_service(request).audit_log(principal, narrow, limit=item.default_rows * 4)
    changes = [e for e in entries if _is_change(e)]
    views = audit_views(request, locale, principal, changes[: item.default_rows])
    rows = [
        Change(v.when, v.actor, v.action, v.target, v.trace_url or "/admin/audit") for v in views
    ]
    return WidgetView(item, list(rows), len(changes), "/admin/audit")


RENDERERS: dict[str, Renderer] = {
    "rights": _rights,
    "bookings-today": _bookings_today,
    "changes": _changes,
}
"""The widgets this step renders; a test checks them against the register."""


def _declared(key: str) -> Widget:
    """Return the register entry of ``key``; a renderer without one is a programming error."""
    found = widget(key)
    if found is None or not found.available:  # pragma: no cover - programming error
        raise LookupError(key)
    return found


async def _views(request: Request, locale: RequestLocale, principal: Principal) -> list[WidgetView]:
    """Render every visible widget of the admin's default layout."""
    allowed = can_function(principal)
    return [await RENDERERS[w.key](request, locale, principal) for w in visible(principal, allowed)]


def _fragment(
    request: Request, locale: RequestLocale, principal: Principal, view: WidgetView
) -> HTMLResponse:
    """Answer one widget without the shell."""
    return render(templates, request, FRAME, locale, {"view": view, "principal": principal})


@router.get("/", response_class=HTMLResponse)
async def overview(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.DASHBOARD_VIEW))],
) -> HTMLResponse:
    """The overview: the visible widgets as a grid, then the groups in scope with shortfalls."""
    principal = context.principal
    views = await _views(request, locale, principal)
    groups = await admin_service(request).groups(principal)
    summaries = await agenda_service(request).summaries(principal)
    shortfalls = {
        g.group.slug: shortfall_view(summaries[g.group.slug], g.group, locale)
        for g in groups
        if g.group.slug in summaries and summaries[g.group.slug].count
    }
    return page(
        request,
        locale,
        "admin/dashboard.html",
        principal,
        {"views": views, "groups": groups, "shortfalls": shortfalls},
    )


@router.get("/overview/w/rights", response_class=FragmentResponse)
async def rights_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.RIGHTS_VIEW))],
) -> HTMLResponse:
    """The fragment *My role and reach*."""
    principal = context.principal
    return _fragment(request, locale, principal, await _rights(request, locale, principal))


@router.get("/overview/w/bookings-today", response_class=FragmentResponse)
async def bookings_today_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.BOOKINGS_VIEW))],
) -> HTMLResponse:
    """The fragment *Bookings today per group*."""
    principal = context.principal
    return _fragment(request, locale, principal, await _bookings_today(request, locale, principal))


@router.get("/overview/w/changes", response_class=FragmentResponse)
async def changes_widget(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.AUDIT_VIEW))],
) -> HTMLResponse:
    """The fragment *Recent configuration changes*."""
    principal = context.principal
    return _fragment(request, locale, principal, await _changes(request, locale, principal))
