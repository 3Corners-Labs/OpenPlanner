"""The widget register of the overview (ADR 0061 §2): every widget declared once.

Each widget of the overview page is a server-rendered fragment with its own route under
``/admin/overview/w/{key}`` and its own permission from the matrix (ADR 0014). This module
declares them: key, i18n title, permission, scope, the views it supports, its default number
of rows, whether it can be live (ADR 0061 §4, a later step) and its manual topic. It also
holds the default layout per role, which ADR 0062 reads as the starting point of a personal
layout.

Like the navigation register (ADR 0047), the register decides *what* exists and *where* it
sits, never *who* may see it: visibility comes only from the matrix, through each widget's
permission. A widget that is declared but not ``available`` yet has no route and is not
rendered; *Alerts* waits for the alert model of OV-6.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from app.domain.authorization import Permission, Principal, Role

OVERVIEW_PATH = "/admin/"
"""The overview page; its route carries ``dashboard.view``."""

FRAGMENT_PREFIX = "/admin/overview/w/"
"""Where the fragments live; the last part is the widget key."""

TOPIC = "overview"
"""The manual topic that explains the overview and its widgets (ADR 0033)."""


class WidgetScope(StrEnum):
    """At which level a widget aggregates: the platform, the own organisations, the own groups."""

    PLATFORM = "platform"
    ORGANISATION = "organisation"
    GROUP = "group"


class View(StrEnum):
    """How a widget may show its data (ADR 0061 §5: charts are server-rendered SVG, OV-4)."""

    LIST = "list"
    TABLE = "table"
    BAR = "bar"
    LINE = "line"
    PIE = "pie"


VIEW_LABELS: dict[View, str] = {
    View.LIST: "admin.overview.view.list",
    View.TABLE: "admin.overview.view.table",
    View.BAR: "admin.overview.view.bar",
    View.LINE: "admin.overview.view.line",
    View.PIE: "admin.overview.view.pie",
}
"""Catalogue key of each view's name, for the links that switch a widget's view."""


@dataclass(frozen=True)
class Widget:
    """One widget of the overview.

    Attributes:
        key: Stable identifier; the last part of the fragment route.
        title_key: The i18n key of its title; a test checks ``nl`` and ``en``.
        permission: The permission its route needs; the matrix decides whether it shows.
        scope: The level it aggregates at.
        views: The views it supports; the first is its default.
        default_rows: How many rows it shows unless a layout (ADR 0062) says otherwise.
        live: Whether the live script (ADR 0061 §4) may refresh it.
        topic: The manual topic that explains it.
        available: Whether this step renders it; without it there is no route (OV-3).
    """

    key: str
    title_key: str
    permission: Permission
    scope: WidgetScope
    views: tuple[View, ...]
    default_rows: int
    live: bool
    topic: str = TOPIC
    available: bool = False

    @property
    def path(self) -> str:
        """Return the fragment route of the widget."""
        return f"{FRAGMENT_PREFIX}{self.key}"


WIDGETS: tuple[Widget, ...] = (
    Widget(
        "alerts",
        "admin.overview.widget.alerts.title",
        Permission.DASHBOARD_VIEW,
        WidgetScope.PLATFORM,
        (View.LIST, View.BAR, View.PIE),
        default_rows=8,
        live=True,
    ),
    Widget(
        "queue",
        "admin.overview.widget.queue.title",
        Permission.QUEUE_WORK,
        WidgetScope.GROUP,
        (View.TABLE, View.BAR, View.LINE),
        default_rows=8,
        live=True,
        available=True,
    ),
    Widget(
        "bookings-today",
        "admin.overview.widget.bookings_today.title",
        Permission.BOOKINGS_VIEW,
        WidgetScope.GROUP,
        (View.LIST, View.BAR, View.PIE),
        default_rows=8,
        live=True,
        available=True,
    ),
    Widget(
        "changes",
        "admin.overview.widget.changes.title",
        Permission.AUDIT_VIEW,
        WidgetScope.GROUP,
        (View.LIST,),
        default_rows=8,
        live=False,
        available=True,
    ),
    Widget(
        "tasks",
        "admin.overview.widget.tasks.title",
        Permission.DASHBOARD_VIEW,
        WidgetScope.PLATFORM,
        (View.LIST,),
        default_rows=5,
        live=True,
        available=True,
    ),
    Widget(
        "status",
        "admin.overview.widget.status.title",
        Permission.SETTINGS_PLATFORM_VIEW,
        WidgetScope.PLATFORM,
        (View.LIST,),
        default_rows=6,
        live=True,
        available=True,
    ),
    Widget(
        "rights",
        "admin.overview.widget.rights.title",
        Permission.RIGHTS_VIEW,
        WidgetScope.PLATFORM,
        (View.LIST,),
        default_rows=6,
        live=False,
        available=True,
    ),
)
"""The seven widgets of ADR 0061 §2, in register order."""

DEFAULT_LAYOUTS: dict[Role, tuple[str, ...]] = {
    Role.SUPERUSER: ("alerts", "queue", "bookings-today", "changes", "tasks", "status", "rights"),
    Role.ORG_ADMIN: ("alerts", "bookings-today", "queue", "changes", "tasks", "rights"),
    Role.GROUP_ADMIN: ("alerts", "bookings-today", "queue", "changes", "rights"),
    # The auditor cares about changes and status; the agent about the queue (ADR 0061).
    Role.AUDITOR: ("changes", "status", "alerts", "rights"),
    Role.AGENT: ("queue", "bookings-today", "alerts", "rights"),
}
"""The default layout per built-in role: widget keys in order (ADR 0062 §2 starts from it)."""

_BY_KEY = {w.key: w for w in WIDGETS}

_ROLE_ORDER = tuple(DEFAULT_LAYOUTS)


def widget(key: str) -> Widget | None:
    """Return the widget called ``key``, if any."""
    return _BY_KEY.get(key)


def fragment_path(item: Widget) -> str:
    """Return the fragment route of ``item`` (``/admin/overview/w/{key}``)."""
    return item.path


def resolve_view(item: Widget, requested: str | None) -> View:
    """Return the view ``item`` draws: ``requested`` when it supports it, else its first.

    The first view in the register is the widget's default (ADR 0061 §2). ``requested`` comes
    from outside (a query parameter, later a personal layout, ADR 0062), so anything that is
    not exactly the name of a supported view, including garbage, is the default.
    """
    try:
        wanted = View(requested or "")
    except ValueError:
        return item.views[0]
    return wanted if wanted in item.views else item.views[0]


def default_layout(principal: Principal) -> tuple[Widget, ...]:
    """Return the widgets of the principal's roles' defaults, merged in role order.

    A principal without a built-in role (custom roles only, ADR 0039) starts from every
    widget in register order; the matrix still decides what shows.
    """
    keys: list[str] = []
    for role in _ROLE_ORDER:
        if role in principal.roles:
            keys += [k for k in DEFAULT_LAYOUTS[role] if k not in keys]
    if not keys:
        return WIDGETS
    return tuple(_BY_KEY[k] for k in keys)


def visible(principal: Principal, allowed: Callable[[Permission], bool]) -> list[Widget]:
    """Return the available widgets of the principal's default layout that ``allowed`` grants.

    ``allowed`` is the matrix (``can_function`` of the admin routes); the register itself
    never decides rights.
    """
    return [w for w in default_layout(principal) if w.available and allowed(w.permission)]


def available() -> Iterator[Widget]:
    """Yield the widgets this step renders, in register order."""
    return (w for w in WIDGETS if w.available)
