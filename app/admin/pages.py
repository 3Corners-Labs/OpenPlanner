"""Shared HTML plumbing of the admin portal: templates, navigation, page and message.

Every admin page goes through ``page`` (portal layout, navigation and ``can()`` from the
matrix); refusals and notices go through ``message``. Route modules (``routes`` for login,
overview and reports, ``group_routes`` for groups and planning) import from here.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from starlette.responses import HTMLResponse

from app.admin.authz import can_function, session_principal
from app.domain.authorization import Permission, Principal, Role
from app.web.authz_labels import ROLE_LABELS
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

    Group pages belong to the overview. An organisation's branding and holiday pages belong
    to their own section (``/admin/organisations/{org}/branding`` to branding,
    ``.../holidays`` to holidays); its other pages to organisations.
    """
    path = request.url.path
    section = path.rsplit("/", 1)[-1] if path.startswith("/admin/organisations/") else ""
    own_section = section in {"branding", "holidays"}
    return (
        path == item.href
        or (item.href == "/admin/" and path.startswith("/admin/groups"))
        or (item.href == "/admin/holidays" and path.startswith("/admin/holidays/"))
        or (own_section and item.href == f"/admin/{section}")
        or (item.href == "/admin/organisations" and bool(section) and not own_section)
    )


def page(
    request: Request,
    locale: RequestLocale,
    name: str,
    principal: Principal,
    context: dict[str, object],
) -> HTMLResponse:
    """Render an admin page with navigation and ``can()`` from the matrix."""
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
        {**context, "principal": principal, "nav": nav, "can": can, "role_labels": role_labels},
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
    }
    principal = session_principal(request)
    if principal is not None:
        response = page(request, locale, "admin/message.html", principal, context)
    else:
        response = render(templates, request, "admin/public_message.html", locale, context)
    response.status_code = status
    return response
