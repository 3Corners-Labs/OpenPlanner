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
from app.admin.session import THEMES, choose_theme, session_theme
from app.domain.authorization import Principal, Role
from app.web.authz_labels import ROLE_LABELS
from app.web.help import HelpTopic, shipped_manual, topic_for_path
from app.web.locale import RequestLocale, resolve_locale
from app.web.navigation import SECTIONS, TOP_BAR, Home, Screen, home_of
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
    return moment_label(at, ZoneInfo(request.app.state.settings.admin_timezone), locale.code)


def page(
    request: Request,
    locale: RequestLocale,
    name: str,
    principal: Principal,
    context: dict[str, object],
) -> HTMLResponse:
    """Render an admin page with navigation and ``can()`` from the matrix."""
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
    role_labels = [locale.gettext(ROLE_LABELS[role]) for role in Role if role in principal.roles]
    theme = choose_theme(request, request.query_params.get(THEME_PARAM))
    return render(
        templates,
        request,
        name,
        locale,
        {
            **context,
            "principal": principal,
            "nav": nav,
            "top_links": top_links,
            "can": can,
            "role_labels": role_labels,
            "theme": theme,
            "themes": theme_options(request, locale, theme),
            # A message page (refusal, not found) keeps ``help_topic`` None: no help link.
            "help_topic": context["help_topic"] if "help_topic" in context else page_topic(request),
            # Set by ``update_routes.load_update_notice`` for every admin route (ADR 0029).
            "update_banner": getattr(request.state, "update_notice", None),
        },
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
        "theme": session_theme(request),
    }
    principal = session_principal(request)
    if principal is not None:
        response = page(request, locale, "admin/message.html", principal, context)
    else:
        response = render(templates, request, "admin/public_message.html", locale, context)
    response.status_code = status
    return response
