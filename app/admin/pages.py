"""Shared HTML plumbing of the admin portal: templates, navigation, page and message.

Every admin page goes through ``page``: the portal layout, the navigation from the register in
``app.web.navigation`` (ADR 0047) filtered by ``can()`` from the matrix. Refusals and notices
go through ``message``. Route modules (``routes`` for login, overview and reports,
``group_routes`` for groups and planning) import from here.
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
from app.admin.frozen_forms import freeze_forms, writable_in_maintenance
from app.admin.session import THEMES, choose_theme, session_theme
from app.domain.authorization import Principal, Role
from app.web.authz_labels import ROLE_LABELS
from app.web.help import HelpTopic, shipped_manual, topic_for_path
from app.web.locale import RequestLocale, resolve_locale
from app.web.navigation import BOTTOM_BAR_ENTRIES, SECTIONS, TOP_BAR, Home, Screen, home_of
from app.web.organisation_settings import settings_for
from app.web.protection import require_same_origin
from app.web.templates import create_templates, render, switch_href

templates = create_templates([Path(__file__).parent / "templates"])

Locale = Annotated[RequestLocale, Depends(resolve_locale)]
SameOrigin = Annotated[None, Depends(require_same_origin)]


@dataclass(frozen=True)
class NavView:
    """A visible navigation entry or tab."""

    label: str
    href: str
    current: bool
    icon: str


@dataclass(frozen=True)
class SectionView:
    """A section of the side navigation with its visible entries."""

    key: str
    label: str
    entries: list[NavView]


THEME_LABELS = {
    "system": "admin.theme.system",
    "light": "admin.theme.light",
    "dark": "admin.theme.dark",
}
"""Label key per theme of ``session.THEMES`` (literal keys, so the catalogue test sees them)."""

THEME_PARAM = "theme"
"""Query parameter of the theme choice in the profile menu."""


def theme_options(request: Request, locale: RequestLocale, current: str) -> list[NavView]:
    """Return the theme choice as query-only links, the current one marked."""
    return [
        NavView(
            locale.gettext(THEME_LABELS[t]), switch_href(request, THEME_PARAM, t), t == current, ""
        )
        for t in THEMES
    ]


def route_template(request: Request) -> str:
    """Return the template of the matched route (``/admin/groups/{slug}``), or ``""``."""
    return str(getattr(request.scope.get("route"), "path", ""))


def current_home(request: Request) -> Home | None:
    """Return where the current page lives in the navigation register (ADR 0047)."""
    return home_of(route_template(request))


def _nav_view(locale: RequestLocale, screen: Screen, home: Home | None) -> NavView:
    """Return the view of a side-navigation entry or top-bar link."""
    current = home is not None and home.entry == screen.key
    return NavView(locale.gettext(screen.label_key), screen.path, current, screen.icon)


def bottom_bar(nav: list[SectionView]) -> tuple[list[NavView], list[SectionView]]:
    """Split the user's menu for the phone bottom bar (backlog SH-1, ADR 0047).

    Returns the first ``BOTTOM_BAR_ENTRIES`` entries in register order, and the remaining entries
    still grouped in their sections for the *More* panel; sections left empty are dropped.
    """
    entries = [entry for section in nav for entry in section.entries]
    shown = entries[:BOTTOM_BAR_ENTRIES]
    rest = [
        replace(section, entries=[e for e in section.entries if e not in shown]) for section in nav
    ]
    return shown, [section for section in rest if section.entries]


def page_topic(request: Request) -> HelpTopic | None:
    """Return the manual topic of the current route, if the screen is documented (ADR 0033)."""
    return topic_for_path(route_template(request))


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
    return moment_label(at, ZoneInfo(settings_for(request).admin_timezone), locale.code)


def role_labels(principal: Principal, locale: RequestLocale) -> list[str]:
    """Name the principal's roles: built-in ones translated, custom ones by their own name."""
    built_in = [locale.gettext(ROLE_LABELS[role]) for role in Role if role in principal.roles]
    custom = sorted(role.name(locale.code) for role in principal.custom_roles)
    return built_in + custom


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
    home = current_home(request)
    sections = [
        SectionView(
            section.key,
            locale.gettext(section.label_key),
            [_nav_view(locale, e, home) for e in section.entries if can(e.permission)],
        )
        for section in SECTIONS
    ]
    nav = [section for section in sections if section.entries]
    top_links = [_nav_view(locale, link, home) for link in TOP_BAR if can(link.permission)]
    bar, more = bottom_bar(nav)
    theme = choose_theme(request, request.query_params.get(THEME_PARAM))
    maintenance = getattr(request.state, "maintenance_notice", None)
    response = render(
        templates,
        request,
        name,
        locale,
        {
            **context,
            "principal": principal,
            "nav": nav,
            "top_links": top_links,
            "bottom_bar": bar,
            "bottom_more": more,
            "can": can,
            "role_labels": role_labels(principal, locale),
            "theme": theme,
            "themes": theme_options(request, locale, theme),
            # A message page (refusal, not found) keeps ``help_topic`` None: no help link.
            "help_topic": context["help_topic"] if "help_topic" in context else page_topic(request),
            # Set by ``update_routes.load_update_notice`` for every admin route (ADR 0029).
            "update_banner": getattr(request.state, "update_notice", None),
            # Set by ``maintenance_routes.load_maintenance_notice`` (ADR 0038).
            "maintenance_notice": maintenance,
        },
        scripts=scripts,
    )
    if maintenance is not None:
        _freeze(request, principal, response)
    return response


def _freeze(request: Request, principal: Principal, response: HTMLResponse) -> None:
    """Disable the buttons of changes that maintenance refuses (ADR 0038), from the matrix."""
    writable = writable_in_maintenance(request.app.routes, principal)
    body = freeze_forms(bytes(response.body).decode(), writable).encode()
    response.body = body
    response.headers["content-length"] = str(len(body))


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
        "theme": session_theme(request),
    }
    principal = session_principal(request)
    if principal is not None:
        response = page(request, locale, "admin/message.html", principal, context)
    else:
        response = render(templates, request, "admin/public_message.html", locale, context)
    response.status_code = status
    return response
