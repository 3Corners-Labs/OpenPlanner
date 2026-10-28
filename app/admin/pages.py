"""Shared HTML plumbing of the admin portal: templates, navigation, page and message.

Every admin page goes through ``page`` (portal layout, navigation and ``can()`` from the
matrix); refusals and notices go through ``message``. Route modules (``routes`` for login,
overview and reports, ``group_routes`` for groups and planning) import from here.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, Request
from starlette.responses import HTMLResponse

from app.admin.authz import can_function, session_principal
from app.admin.forms import Field, moment_label
from app.domain.authorization import Permission, Principal, Role
from app.web.authz_labels import ROLE_LABELS
from app.web.help import HELP_PATH, HelpTopic, shipped_manual, topic_for_path
from app.web.locale import RequestLocale, resolve_locale
from app.web.protection import require_same_origin
from app.web.templates import create_templates, render

templates = create_templates([Path(__file__).parent / "templates"])

Locale = Annotated[RequestLocale, Depends(resolve_locale)]
SameOrigin = Annotated[None, Depends(require_same_origin)]


@dataclass(frozen=True)
class NavItem:
    """One entry of the admin navigation, shown only if the matrix allows it."""

    label_key: str
    href: str
    permission: Permission
    icon: str


NAVIGATION = (
    NavItem("admin.nav.dashboard", "/admin/", Permission.DASHBOARD_VIEW, "dashboard"),
    NavItem("admin.nav.audit", "/admin/audit", Permission.AUDIT_VIEW, "audit"),
    NavItem("admin.nav.retention", "/admin/retention", Permission.RETENTION_VIEW, "clock"),
    NavItem("admin.nav.rights", "/admin/rights", Permission.RIGHTS_VIEW, "rights"),
    NavItem("admin.nav.matrix", "/admin/matrix", Permission.MATRIX_VIEW, "matrix"),
    NavItem(
        "admin.nav.integrations", "/admin/integrations", Permission.INTEGRATIONS_MANAGE, "settings"
    ),
    NavItem(
        "admin.nav.organisations",
        "/admin/organisations",
        Permission.ORGANISATION_MANAGE,
        "organisation",
    ),
    NavItem("admin.nav.branding", "/admin/branding", Permission.BRANDING_MANAGE, "palette"),
    NavItem("admin.nav.holidays", "/admin/holidays", Permission.HOLIDAYS_ASSIGN, "holiday"),
    NavItem("admin.nav.closures", "/admin/closures", Permission.ORGANISATION_CLOSURE, "alert"),
    NavItem("admin.nav.api", "/admin/api", Permission.API_CLIENTS_MANAGE, "key"),
    NavItem("admin.nav.updates", "/admin/updates", Permission.SYSTEM_UPDATE, "refresh"),
    NavItem("admin.nav.help", HELP_PATH, Permission.DASHBOARD_VIEW, "help"),
)


@dataclass(frozen=True)
class NavView:
    """A visible navigation entry."""

    label: str
    href: str
    current: bool
    icon: str


def _is_current(request: Request, item: NavItem) -> bool:
    """Return whether ``item`` is the current section.

    Group pages belong to the overview. An organisation's branding, holiday and closure pages
    belong to their own section (``/admin/organisations/{org}/branding`` to branding,
    ``.../holidays`` to holidays, ``.../closure`` to closures), its channel pages to
    integrations, its other pages to organisations.
    """
    path = request.url.path
    organisation_page = path.startswith("/admin/organisations/")
    section = path.rsplit("/", 1)[-1] if organisation_page else ""
    sections = {"branding": "branding", "holidays": "holidays", "closure": "closures"}
    channels = organisation_page and "/channels" in path
    return (
        path == item.href
        or (item.href == "/admin/" and path.startswith("/admin/groups"))
        or (item.href == "/admin/holidays" and path.startswith("/admin/holidays/"))
        or (section in sections and item.href == f"/admin/{sections[section]}")
        or (item.href == "/admin/integrations" and channels)
        or (
            item.href == "/admin/organisations"
            and organisation_page
            and section not in sections
            and not channels
        )
        or (item.href == "/admin/api" and path.startswith("/admin/api/"))
        or (item.href == HELP_PATH and path.startswith(f"{HELP_PATH}/"))
    )


def page_topic(request: Request) -> HelpTopic | None:
    """Return the manual topic of the current route, if the screen is documented (ADR 0033)."""
    return topic_for_path(getattr(request.scope.get("route"), "path", ""))


def with_manual(request: Request, locale: RequestLocale, fields: Sequence[Field]) -> list[Field]:
    """Return ``fields`` with their manual section attached, for the (i) disclosures.

    The topic follows from the route, so a route module only passes its fields through.
    """
    found = page_topic(request)
    manual = shipped_manual().page(found.slug, locale.code) if found else None
    if manual is None:
        return list(fields)
    return [
        replace(f, more=more, more_link=manual.link(f.name)) if (more := manual.more(f.name)) else f
        for f in fields
    ]


def admin_moment(request: Request, locale: RequestLocale, at: datetime) -> str:
    """Return a compact local date and time in the admin time zone (``ADMIN_TIMEZONE``)."""
    return moment_label(at, ZoneInfo(request.app.state.settings.admin_timezone), locale.code)


def page(
    request: Request,
    locale: RequestLocale,
    name: str,
    principal: Principal,
    context: dict[str, object],
    *,
    scripts: bool = False,
) -> HTMLResponse:
    """Render an admin page with navigation and ``can()`` from the matrix.

    ``scripts=True`` allows the page's own scripts (``SCRIPT_CSP``); only pages that enhance
    themselves under an ADR do (the updates page, ADR 0048).
    """
    can = can_function(principal)
    nav = [
        NavView(locale.gettext(item.label_key), item.href, _is_current(request, item), item.icon)
        for item in NAVIGATION
        if can(item.permission)
    ]
    role_labels = [locale.gettext(ROLE_LABELS[role]) for role in Role if role in principal.roles]
    return render(
        templates,
        request,
        name,
        locale,
        {
            **context,
            "principal": principal,
            "nav": nav,
            "can": can,
            "role_labels": role_labels,
            # A message page (refusal, not found) keeps ``help_topic`` None: no help link.
            "help_topic": context["help_topic"] if "help_topic" in context else page_topic(request),
            # Set by ``update_routes.load_update_notice`` for every admin route (ADR 0029).
            "update_banner": getattr(request.state, "update_notice", None),
        },
        scripts=scripts,
    )


def message(
    request: Request,
    locale: RequestLocale,
    status: int,
    title_key: str,
    message_key: str,
    *,
    back_link: bool = True,
) -> HTMLResponse:
    """Render a message page; with a logged-in admin inside the portal layout and menu.

    ``back_link=False`` drops the link to the overview, for visitors it would only loop back.
    """
    context: dict[str, object] = {
        "title": locale.gettext(title_key),
        "message": locale.gettext(message_key),
        "back_link": back_link,
        "help_topic": None,
    }
    principal = session_principal(request)
    if principal is not None:
        response = page(request, locale, "admin/message.html", principal, context)
    else:
        response = render(templates, request, "admin/public_message.html", locale, context)
    response.status_code = status
    return response
