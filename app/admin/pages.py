"""Shared HTML plumbing of the admin portal: templates, navigation, page and message.

Every admin page goes through ``page``: the portal layout, the navigation from the register in
``app.web.navigation`` (ADR 0047) filtered by ``can()`` from the matrix. Refusals and notices
go through ``message``. Route modules (``routes`` for login, overview and reports,
``group_routes`` for groups and planning) import from here.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.admin.authz import can_function, return_path, session_principal
from app.admin.forms import Field, moment_label
from app.admin.frozen_forms import freeze_forms, writable_in_maintenance
from app.admin.session import THEMES, session_alert_prefs, session_nav, session_theme
from app.admin.top_bar import top_links
from app.domain.authorization import Permission, Principal, Role, Scope, scope_for
from app.i18n import BoundGettext
from app.web.authz_labels import ROLE_LABELS
from app.web.help import HelpTopic, shipped_manual, topic_for_path
from app.web.locale import RequestLocale, request_locale
from app.web.navigation import (
    BOTTOM_BAR_ENTRIES,
    SECTIONS,
    Home,
    ObjectPage,
    Screen,
    Width,
    entry,
    home_of,
    object_page,
)
from app.web.organisation_settings import settings_for
from app.web.protection import require_same_origin
from app.web.templates import create_templates, kept_query, render
from app.web.widgets import visible

templates = create_templates([Path(__file__).parent / "templates"])

Locale = Annotated[RequestLocale, Depends(request_locale)]
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


@dataclass(frozen=True)
class ObjectHeader:
    """The header of an organisation page (ADR 0047 step 3).

    Attributes:
        name: The organisation's name, for the heading and the breadcrumbs.
        slug: Its slug, for links.
        tabs: The visible tabs, the current one marked.
        subtabs: The second row of the current tab (its own page first), or empty.
    """

    name: str
    slug: str
    tabs: list[NavView]
    subtabs: list[NavView]


ORGANISATIONS_PATH = "/admin/organisations"
"""The organisations list, where old per-organisation lists redirect to (ADR 0047 §4)."""


def object_tabs(
    locale: RequestLocale,
    obj: ObjectPage,
    slug: str,
    can: Callable[[Permission], bool],
    home: Home | None,
) -> tuple[list[NavView], list[NavView]]:
    """Return the visible tabs of ``obj`` for the object ``slug``, and the current tab's row.

    A tab or sub-tab shows only if the matrix grants its permission (``can``). The second row
    holds the current tab's own page followed by its visible sub-tabs, and is empty when the
    current tab has no visible sub-tab.
    """
    placeholder = obj.path.rsplit("/", 1)[-1]

    def view(screen: Screen, current: bool) -> NavView:
        return NavView(
            locale.gettext(screen.label_key),
            screen.path.replace(placeholder, slug),
            current,
            screen.icon,
        )

    tabs = [
        view(tab, home is not None and home.tab == tab.key)
        for tab in obj.tabs
        if can(tab.permission)
    ]
    current = next((tab for tab in obj.tabs if home is not None and home.tab == tab.key), None)
    if current is None or not can(current.permission):
        return tabs, []
    row = [
        view(sub, home is not None and home.subtab == sub.key)
        for sub in current.subtabs
        if can(sub.permission)
    ]
    if not row:
        return tabs, []
    return tabs, [view(current, home is not None and home.subtab is None), *row]


def entry_subtabs(
    locale: RequestLocale, can: Callable[[Permission], bool], home: Home | None
) -> list[NavView]:
    """Return the second row of the current side-navigation entry (ADR 0099), or empty.

    Like the row of an object tab: the entry's own page first, then the sub-tabs the matrix
    allows; empty when the entry has no visible sub-tab, or the page lives on an object tab.
    """
    screen = entry(home.entry) if home is not None and home.tab is None else None
    if home is None or screen is None:
        return []
    row = [
        NavView(locale.gettext(sub.label_key), sub.path, home.subtab == sub.key, sub.icon)
        for sub in screen.subtabs
        if can(sub.permission)
    ]
    if not row:
        return []
    own = NavView(
        locale.gettext(screen.own_label_key or screen.label_key),
        screen.path,
        home.subtab is None,
        screen.icon,
    )
    return [own, *row]


def organisation_header(
    request: Request, locale: RequestLocale, principal: Principal, name: str, slug: str
) -> ObjectHeader:
    """Return the header of organisation ``slug``'s pages, with the tabs the matrix allows."""
    tabs, subtabs = object_tabs(
        locale, object_page("organisation"), slug, can_function(principal), current_home(request)
    )
    return ObjectHeader(name, slug, tabs, subtabs)


def organisation_redirect(
    principal: Principal, permission: Permission, tab: str
) -> RedirectResponse:
    """Send an old per-organisation list to its new home, permanently (ADR 0047 §4).

    An admin whose scope for ``permission`` is exactly one organisation lands on that
    organisation's ``tab``; everyone else on the organisations list. The location is built
    from the authorised scope only, never from the request.
    """
    scope = scope_for(principal, permission)
    if not scope.everything and len(scope.organisations) == 1:
        (slug,) = scope.organisations
        return RedirectResponse(f"{ORGANISATIONS_PATH}/{slug}/{tab}", status_code=308)
    return RedirectResponse(ORGANISATIONS_PATH, status_code=308)


THEME_LABELS = {
    "system": "admin.theme.system",
    "light": "admin.theme.light",
    "dark": "admin.theme.dark",
}
"""Label key per theme of ``session.THEMES`` (literal keys, so the catalogue test sees them)."""


@dataclass(frozen=True)
class Choice:
    """One option of a choice row in the profile menu (theme), rendered as a submit button."""

    value: str
    label: str
    current: bool


ALERT_POPUP_LABELS = {
    "all": "admin.alerts.popups.all",
    "warnings": "admin.alerts.popups.warnings",
    "outages": "admin.alerts.popups.outages",
    "off": "admin.alerts.popups.off",
}
"""Label key per pop-up choice of ``session.ALERT_POPUPS`` (literal keys for the catalogue)."""


def back_href(request: Request) -> str:
    """Return the current page as the "return to" target of the profile menu's forms.

    The path is ``return_path`` (a GET returns to itself, a page rendered by a POST to the page
    that sent the form) and the query is filtered like a link's (``kept_query``), so typed
    input is never echoed; ``preference_routes`` still checks the value with ``safe_next``.
    """
    query = urlencode(kept_query(request))
    path = return_path(request)
    return f"{path}?{query}" if query else path


def theme_options(locale: RequestLocale, current: str) -> list[Choice]:
    """Return the theme choice of the profile menu, the current one marked."""
    return [Choice(t, locale.gettext(THEME_LABELS[t]), t == current) for t in THEMES]


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


def scope_text(scope: Scope | None, _: BoundGettext) -> str:
    """Describe a scope in words, for the rights page and the "My role and reach" widget."""
    if scope is None:
        return _("admin.rights.scope_portal")
    if scope.everything:
        return _("admin.rights.scope_everything")
    parts = []
    if scope.organisations:
        parts.append(
            _("admin.rights.scope_organisations", names=", ".join(sorted(scope.organisations)))
        )
    if scope.groups:
        parts.append(_("admin.rights.scope_groups", names=", ".join(sorted(scope.group_slugs))))
    return "; ".join(parts)


def role_name(role: Role, locale: RequestLocale, *, with_standard: bool = False) -> str:
    """Return the name of a built-in role: the organisation's own, else the shipped one.

    With ``with_standard`` a name that differs from the shipped one is followed by it in
    brackets, wherever the name stands next to rights, so a reader of the rights matrix or of
    "My rights" can always tell which role is meant (ADR 0077).
    """
    key = ROLE_LABELS[role]
    chosen = locale.gettext(key)
    if not with_standard:
        return chosen
    standard = locale.translator.gettext(locale.code, key)
    return chosen if chosen == standard else f"{chosen} ({standard})"


def role_labels(
    principal: Principal, locale: RequestLocale, *, with_standard: bool = False
) -> list[str]:
    """Name the principal's roles: built-in ones translated, custom ones by their own name."""
    built_in = [
        role_name(role, locale, with_standard=with_standard)
        for role in Role
        if role in principal.roles
    ]
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
    on_overview = {item.key for item in visible(principal, can)}
    topic: object = context["help_topic"] if "help_topic" in context else page_topic(request)
    help_topic = topic if isinstance(topic, HelpTopic) else None
    bar, more = bottom_bar(nav)
    theme = session_theme(request)
    maintenance = getattr(request.state, "maintenance_notice", None)
    response = render(
        templates,
        request,
        name,
        locale,
        {
            **context,
            "principal": principal,
            # The page width from the register (ADR 0080); ``admin.css`` keys on it.
            "content_width": (home.width if home else Width.STANDARD).value,
            # The second row of an entry with sub-tabs (ADR 0099); ``_subtabs.html`` shows it.
            "entry_tabs": entry_subtabs(locale, can, home),
            "nav": nav,
            "top_links": top_links(request, locale.gettext, can, on_overview, home, help_topic),
            "bottom_bar": bar,
            "bottom_more": more,
            "can": can,
            "role_labels": role_labels(principal, locale),
            "theme": theme,
            "themes": theme_options(locale, theme),
            # The menu width the admin chose from the top bar's menu button, if any.
            "nav_choice": session_nav(request),
            "alert_prefs": session_alert_prefs(request),
            # Where the preference forms of the profile menu return to (``preference_routes``).
            "back": back_href(request),
            "alert_popup_options": [
                (key, locale.gettext(label)) for key, label in ALERT_POPUP_LABELS.items()
            ],
            # A message page (refusal, not found) keeps ``help_topic`` None: no help link.
            "help_topic": help_topic,
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
