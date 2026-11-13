"""Shared HTML plumbing of the admin portal: templates, navigation, page and message.

Every admin page goes through ``page``: the portal layout, the navigation from the register in
``app.web.navigation`` (ADR 0047) filtered by ``can()`` from the matrix. Refusals and notices
go through ``message``. Route modules (``routes`` for login, overview and reports,
``group_routes`` for groups and planning) import from here.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.admin.authz import can_function, overview_layout, session_principal
from app.admin.forms import Field, moment_label
from app.admin.frozen_forms import freeze_forms, writable_in_maintenance
from app.admin.return_to import page_path
from app.admin.session import (
    THEMES,
    current_session,
    session_alert_prefs,
    session_nav,
    session_theme,
)
from app.admin.top_bar import top_links
from app.domain.authorization import Permission, Principal, Role, Scope, scope_for
from app.domain.scheduling import GroupSummary, Organisation
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
from app.web.security_policy import PageKind
from app.web.templates import create_templates, kept_query, render
from app.web.widgets import on_overview as overview_widgets

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
    shows: Callable[[Screen], bool],
    home: Home | None,
) -> tuple[list[NavView], list[NavView]]:
    """Return the visible tabs of ``obj`` for the object ``slug``, and the current tab's row.

    A tab or sub-tab shows only if ``shows`` says so: the matrix grants its permission, and
    the caller may add a rule of the object (the own queue of a group, ADR 0043). A tab with
    sub-tabs also shows when only one of its sub-tabs is granted (ADR 0105): it then leads to
    the first page the admin may open, so nobody loses the way to a page they were allowed.
    The second row holds the current tab's own page, when granted, followed by its visible
    sub-tabs, and is empty when it would hold fewer than two.
    """
    placeholder = obj.path.rsplit("/", 1)[-1]

    def view(
        screen: Screen, current: bool, target: Screen | None = None, own: bool = False
    ) -> NavView:
        label = screen.own_label_key if own and screen.own_label_key else screen.label_key
        return NavView(
            locale.gettext(label),
            (target or screen).path.replace(placeholder, slug),
            current,
            screen.icon,
        )

    def first_page(tab: Screen) -> Screen | None:
        """Return the first page of ``tab`` the matrix grants, its own page first."""
        return next((s for s in (tab, *tab.subtabs) if shows(s)), None)

    tabs = [
        view(tab, home is not None and home.tab == tab.key, first_page(tab))
        for tab in obj.tabs
        if first_page(tab) is not None
    ]
    current = next((tab for tab in obj.tabs if home is not None and home.tab == tab.key), None)
    if current is None or first_page(current) is None:
        return tabs, []
    row = [
        view(sub, home is not None and home.subtab == sub.key)
        for sub in current.subtabs
        if shows(sub)
    ]
    if shows(current):
        row.insert(0, view(current, home is not None and home.subtab is None, own=True))
    return tabs, row if len(row) > 1 else []


def entry_subtabs(
    locale: RequestLocale, can: Callable[[Permission], bool], home: Home | None
) -> list[NavView]:
    """Return the second row of the current side-navigation entry (ADR 0101), or empty.

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


OWN_QUEUE_TABS = frozenset({"group.queue"})
"""Screens shown only on a group with an own queue: it dispatches there (ADR 0043) or falls back
to it (ADR 0082 point 11)."""


def group_shows(principal: Principal, summary: GroupSummary) -> Callable[[Screen], bool]:
    """Return whether a screen of the register shows for ``principal`` on this group's pages.

    The one predicate for the group's tabs and the links of its readiness checklist: the
    matrix grants the screen's permission on this group (or its organisation), and the own
    queue shows only on a group that dispatches or falls back to it (ADR 0043, ADR 0082).
    """
    can = can_function(principal)

    def shows(screen: Screen) -> bool:
        return bool(can(screen.permission, summary)) and (
            summary.group.queue_open or screen.key not in OWN_QUEUE_TABS
        )

    return shows


def organisation_shows(principal: Principal, slug: str) -> Callable[[Screen], bool]:
    """Return whether a screen shows for ``principal`` on organisation ``slug``'s pages.

    The one predicate for the organisation's tabs and the links of its readiness checklist;
    an organisation-level permission is decided on this organisation.
    """
    can = can_function(principal)
    return lambda screen: bool(can(screen.permission, organisation=slug))


def organisation_header(
    request: Request, locale: RequestLocale, principal: Principal, name: str, slug: str
) -> ObjectHeader:
    """Return the header of organisation ``slug``'s pages, with the tabs the matrix allows."""
    tabs, subtabs = object_tabs(
        locale,
        object_page("organisation"),
        slug,
        organisation_shows(principal, slug),
        current_home(request),
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


def org_page(
    request: Request,
    locale: RequestLocale,
    principal: Principal,
    organisation: Organisation,
    template: str,
    extra: Mapping[str, object],
    *,
    status: int = 200,
) -> HTMLResponse:
    """Render an organisation's page: its header with the tabs the matrix allows, then ``extra``.

    The one home of what every organisation page of a list or a form needs: ``organisation`` and
    ``org_header`` reach the template next to the page's own values in ``extra`` (a form's
    ``errors``, a confirmation's ``done``, ...), and the response gets ``status`` (422 for a
    refused form).
    """
    response = page(
        request,
        locale,
        template,
        principal,
        {
            "organisation": organisation,
            "org_header": organisation_header(
                request, locale, principal, organisation.name, organisation.slug
            ),
            **extra,
        },
    )
    response.status_code = status
    return response


ARCHIVED_VALUE = "archived"
"""The value of an archived filter (``?skills=archived``) in a list that has more than one."""


def archived_requested(request: Request, name: str) -> bool:
    """Return whether the list asks for its archived rows (``?<name>=archived``)."""
    return request.query_params.get(name) == ARCHIVED_VALUE


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

    The path is ``page_path`` (a GET returns to itself, a page rendered by a POST or by a
    download to the page that asked for it) and the query is filtered like a link's
    (``kept_query``), so typed input is never echoed into the form; ``preference_routes``
    still checks the value with ``safe_next``.
    """
    query = urlencode(kept_query(request))
    path = page_path(request)
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


def _signed_in_key(request: Request) -> str:
    """Return the moment of this session's sign-in as epoch seconds, or empty without one.

    It names the sign-in for ``brand-motion.js`` only (play once per sign-in); a time, no
    secret and no personal data.
    """
    found = current_session(request)
    return str(int(found.login_at.timestamp())) if found is not None else ""


def page(
    request: Request,
    locale: RequestLocale,
    name: str,
    principal: Principal,
    context: dict[str, object],
    *,
    kind: PageKind = PageKind.DOCUMENT,
    help_link: bool = True,
) -> HTMLResponse:
    """Render an admin page with navigation and ``can()`` from the matrix.

    The menu, the top bar and the profile menu reach the template as ``render``'s ``frame``, so
    a page value under one of their names is refused instead of silently replaced.
    ``kind=PageKind.SCRIPTED`` allows the page's own scripts (``SCRIPT_CSP``); only pages that
    enhance themselves under an ADR do (the updates page, ADR 0048). ``help_link=False`` leaves
    out the top bar's link to the manual (a message page: refusal, not found).
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
    on_overview = {item.key for item in overview_widgets(principal, can, overview_layout(request))}
    help_topic = page_topic(request) if help_link else None
    bar, more = bottom_bar(nav)
    theme = session_theme(request)
    maintenance = getattr(request.state, "maintenance_notice", None)
    response = render(
        templates,
        request,
        name,
        locale,
        context,
        frame={
            "principal": principal,
            # The page width from the register (ADR 0080); ``admin.css`` keys on it.
            "content_width": (home.width if home else Width.STANDARD).value,
            # The second row of an entry with sub-tabs (ADR 0101); ``_subtabs.html`` shows it.
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
            # Where the forms of the profile menu and the alert banner return to
            # (``preference_routes``); a page's own link back (to the agenda) is its ``back``.
            "return_to": back_href(request),
            "alert_popup_options": [
                (key, locale.gettext(label)) for key, label in ALERT_POPUP_LABELS.items()
            ],
            # ADR 0060 §7: which sign-in the top-bar mark settles for once (brand-motion.js).
            "brand_signed_in": _signed_in_key(request),
            # Set by ``update_routes.load_update_notice`` for every admin route (ADR 0029).
            "update_banner": getattr(request.state, "update_notice", None),
            # Set by ``maintenance_routes.load_maintenance_notice`` (ADR 0038).
            "maintenance_notice": maintenance,
            # Set by ``alerts.load_alerts`` for every admin page (ADR 0061 §7).
            "alert_banner": getattr(request.state, "alert_banner", None),
        },
        kind=kind,
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
    }
    principal = session_principal(request)
    if principal is not None:
        response = page(request, locale, "admin/message.html", principal, context, help_link=False)
    else:
        context["theme"] = session_theme(request)  # the shell's theme, without the shell
        response = render(templates, request, "admin/public_message.html", locale, context)
    response.status_code = status
    return response
