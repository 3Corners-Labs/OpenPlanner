"""The navigation register of the admin portal: where every screen lives (ADR 0047).

One declaration of the portal's structure: sections of the side navigation with their entries,
the top bar, and object pages with their tabs. Every admin page route belongs to exactly one
entry or tab, as its main path or as a sub-page (a detail or edit page). The side navigation,
the object tabs and ``docs/admin-map.md`` (``python -m app.cli admin-map``) are rendered from
it, and a test fails when an admin page has no home here.

The register decides *where* a screen lives, never *who* may open it: visibility comes only
from the authorisation matrix (ADR 0014), through each screen's permission.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from app.domain.authorization import Permission
from app.i18n import Translator

MAX_SECTIONS = 3
"""Sections of the side navigation; more needs an ADR (ADR 0047 §7, a design rule)."""

MAX_TABS = 6
"""Top-level tabs per object page; more needs an ADR (ADR 0047 §7, a design rule)."""

BOTTOM_BAR_ENTRIES = 4
"""Entries of the user's menu shown directly in the phone bottom bar; the rest sit behind *More*.

A design-system value like ``MAX_SECTIONS`` (backlog SH-1), not a setting: it follows from the
width of a phone screen and the target size, and it never changes which screens a user sees.
"""


@dataclass(frozen=True)
class Screen:
    """One side-navigation entry, top-bar link or tab.

    Attributes:
        key: Stable identifier, ``section.name`` for entries, ``object.name`` for tabs.
        label_key: The i18n key of its label.
        path: The route template it links to.
        permission: The permission it needs; the matrix decides whether it shows.
        icon: The icon name from ``admin/_icons.html``.
        subpages: Further route templates that belong here (detail and edit pages).
        target: For a top-bar shortcut: the key of the entry it leads into. A shortcut links to
            a place that already has a home, so it is the home of no route (``path`` may carry
            a query or a fragment); the rule "one home per screen" (ADR 0047) stays whole.
        widget: For a shortcut to an overview widget: that widget's key. The link shows only
            while the widget is on the admin's overview, so it never leads nowhere.
    """

    key: str
    label_key: str
    path: str
    permission: Permission
    icon: str
    subpages: tuple[str, ...] = ()
    target: str = ""
    widget: str = ""

    @property
    def routes(self) -> tuple[str, ...]:
        """Return every route template this screen is the home of (none for a shortcut)."""
        return () if self.target else (self.path, *self.subpages)


@dataclass(frozen=True)
class Section:
    """A titled group of side-navigation entries."""

    key: str
    label_key: str
    entries: tuple[Screen, ...]


@dataclass(frozen=True)
class ObjectPage:
    """The pages of one object (a group, later an organisation), shown as tabs.

    Attributes:
        key: Stable identifier of the object kind.
        label_key: The i18n key of the object kind's name.
        path: The route template of the object's own page; every tab lives under it.
        entry: The side-navigation entry that is current on the object's pages.
        tabs: The tabs, in order.
    """

    key: str
    label_key: str
    path: str
    entry: str
    tabs: tuple[Screen, ...]


@dataclass(frozen=True)
class Home:
    """Where a route lives: the side-navigation entry and, on an object page, the tab."""

    entry: str
    tab: str | None


SECTIONS = (
    Section(
        "monitor",
        "admin.nav.section.monitor",
        (
            Screen(
                "monitor.overview",
                "admin.nav.dashboard",
                "/admin/",
                Permission.DASHBOARD_VIEW,
                "dashboard",
                ("/admin/groups/new",),
            ),
            Screen(
                "monitor.audit", "admin.nav.audit", "/admin/audit", Permission.AUDIT_VIEW, "audit"
            ),
            Screen(
                "monitor.retention",
                "admin.nav.retention",
                "/admin/retention",
                Permission.RETENTION_VIEW,
                "clock",
            ),
        ),
    ),
    Section(
        "manage",
        "admin.nav.section.manage",
        (
            Screen(
                "manage.organisations",
                "admin.nav.organisations",
                "/admin/organisations",
                Permission.ORGANISATION_MANAGE,
                "organisation",
                (
                    "/admin/organisations/new",
                    "/admin/organisations/{org}",
                    "/admin/organisations/{org}/contacts",
                ),
            ),
            Screen(
                "manage.branding",
                "admin.nav.branding",
                "/admin/branding",
                Permission.BRANDING_MANAGE,
                "palette",
                ("/admin/organisations/{org}/branding",),
            ),
            Screen(
                "manage.embedding",
                "admin.nav.embedding",
                "/admin/embedding",
                Permission.EMBEDDING_MANAGE,
                "code",
                ("/admin/organisations/{org}/embedding",),
            ),
            # ADR 0042 step 5: the class D settings of the own organisations.
            Screen(
                "manage.organisation_settings",
                "admin.nav.organisation_settings",
                "/admin/organisation-settings",
                Permission.SETTINGS_ORGANISATION_MANAGE,
                "sliders",
                (
                    "/admin/organisations/{org}/settings",
                    # ADR 0074: the organisation's own wording of citizen pages and messages.
                    "/admin/organisations/{org}/texts",
                    "/admin/organisations/{org}/texts/{text_locale}",
                ),
            ),
            Screen(
                "manage.holidays",
                "admin.nav.holidays",
                "/admin/holidays",
                Permission.HOLIDAYS_ASSIGN,
                "holiday",
                ("/admin/holidays/{calendar}", "/admin/organisations/{org}/holidays"),
            ),
            Screen(
                "manage.closures",
                "admin.nav.closures",
                "/admin/closures",
                Permission.ORGANISATION_CLOSURE,
                "alert",
                (
                    "/admin/organisations/{org}/closure",
                    "/admin/organisations/{org}/closure/scenarios",
                    "/admin/organisations/{org}/closure/scenarios/{scenario_id}",
                    "/admin/organisations/{org}/closure/scenarios/{scenario_id}/contacts",
                ),
            ),
            Screen(
                "manage.staff",
                "admin.nav.staff",
                "/admin/staff",
                Permission.STAFF_VIEW,
                "people",
                ("/admin/staff/{member_id}",),
            ),
            # ADR 0049 point 17: the values the identity provider must send, read-only.
            Screen(
                "manage.idp_mapping",
                "admin.nav.idp_mapping",
                "/admin/idp-mapping",
                Permission.IDP_MAPPING_VIEW,
                "key",
            ),
            Screen(
                "manage.rights",
                "admin.nav.rights",
                "/admin/rights",
                Permission.RIGHTS_VIEW,
                "rights",
            ),
            Screen(
                "manage.matrix",
                "admin.nav.matrix",
                "/admin/matrix",
                Permission.MATRIX_VIEW,
                "matrix",
                # The role editor opens from the matrix (ADR 0039 step 3).
                ("/admin/roles", "/admin/roles/new", "/admin/roles/{role_id}"),
            ),
        ),
    ),
    Section(
        "platform",
        "admin.nav.section.platform",
        (
            Screen(
                "platform.integrations",
                "admin.nav.integrations",
                "/admin/integrations",
                Permission.INTEGRATIONS_MANAGE,
                "settings",
                ("/admin/organisations/{org}/channels",),
            ),
            Screen(
                "platform.api",
                "admin.nav.api",
                "/admin/api",
                Permission.API_CLIENTS_MANAGE,
                "key",
                ("/admin/api/clients/new", "/admin/api/clients/{client_id}"),
            ),
            Screen(
                "platform.updates",
                "admin.nav.updates",
                "/admin/updates",
                Permission.SYSTEM_UPDATE,
                "refresh",
            ),
            Screen(
                "platform.maintenance",
                "admin.nav.maintenance",
                "/admin/maintenance",
                Permission.SYSTEM_MAINTENANCE,
                "lock",
            ),
            # ADR 0042 §11: System status and Advanced settings; the auditor reads them too.
            Screen(
                "platform.settings",
                "admin.nav.settings",
                "/admin/settings",
                Permission.SETTINGS_PLATFORM_VIEW,
                "sliders",
            ),
        ),
    ),
)
"""The side navigation, in order."""

TOP_BAR = (
    # Shortcuts (ADR 0047 stays whole: they own no route). Status opens System status; tasks
    # the overview widget of the same name; alerts the log from warnings up, until the alert
    # model of ADR 0061 step 4 brings its own list.
    Screen(
        "top.status",
        "admin.nav.status",
        "/admin/settings",
        Permission.SETTINGS_PLATFORM_VIEW,
        "status",
        target="platform.settings",
    ),
    Screen(
        "top.tasks",
        "admin.nav.tasks",
        "/admin/#widget-tasks",
        Permission.DASHBOARD_VIEW,
        "tasks",
        target="monitor.overview",
        widget="tasks",
    ),
    Screen(
        "top.alerts",
        "admin.nav.alerts",
        "/admin/audit?level=warning",
        Permission.AUDIT_VIEW,
        "bell",
        target="monitor.audit",
    ),
    Screen(
        "top.help",
        "admin.nav.help",
        "/admin/help",
        Permission.DASHBOARD_VIEW,
        "help",
        ("/admin/help/{topic}",),
    ),
)
"""Links in the top bar before the profile: three shortcuts, then the manual."""

_GROUP = "/admin/groups/{slug}"

OBJECTS = (
    ObjectPage(
        "group",
        "admin.nav.object.group",
        _GROUP,
        "monitor.overview",
        (
            Screen(
                "group.overview", "admin.tab.overview", _GROUP, Permission.GROUP_VIEW, "dashboard"
            ),
            Screen(
                "group.settings",
                "admin.tab.settings",
                f"{_GROUP}/settings",
                Permission.GROUP_EDIT,
                "settings",
                # ADR 0055: the booking form is a sub-page of the settings.
                (f"{_GROUP}/settings/form", f"{_GROUP}/settings/form/{{key}}"),
            ),
            Screen(
                "group.staffing",
                "admin.tab.hours",
                f"{_GROUP}/staffing",
                Permission.SCHEDULE_MANAGE,
                "clock",
                (f"{_GROUP}/staffing/{{rule_id}}", f"{_GROUP}/staffing/{{rule_id}}/split"),
            ),
            Screen(
                "group.exceptions",
                "admin.tab.exceptions",
                f"{_GROUP}/exceptions",
                Permission.SCHEDULE_MANAGE,
                "calendar",
            ),
            Screen(
                "group.holidays",
                "admin.tab.holidays",
                f"{_GROUP}/holidays",
                Permission.SCHEDULE_MANAGE,
                "holiday",
            ),
            Screen(
                "group.reminders",
                "admin.tab.reminders",
                f"{_GROUP}/reminders",
                Permission.REMINDERS_MANAGE,
                "bell",
            ),
            Screen(
                "group.messages",
                "admin.tab.messages",
                f"{_GROUP}/messages",
                Permission.TEXTS_MANAGE,
                "mail",
                (f"{_GROUP}/messages/{{text_locale}}",),
            ),
            Screen(
                "group.agenda",
                "admin.tab.agenda",
                f"{_GROUP}/agenda",
                Permission.BOOKINGS_VIEW,
                "agenda",
                (
                    f"{_GROUP}/bookings/{{booking_id}}",
                    f"{_GROUP}/agenda/week",
                    f"{_GROUP}/agenda/bookings/{{booking_id}}/move",
                    f"{_GROUP}/agenda/bookings/{{booking_id}}/cancel",
                    f"{_GROUP}/agenda/bookings/{{booking_id}}/rebook",
                    f"{_GROUP}/agenda/slot-rebook",
                    f"{_GROUP}/agenda/waiting",
                ),
            ),
            Screen(
                "group.queue",
                "admin.tab.queue",
                f"{_GROUP}/queue",
                Permission.QUEUE_WORK,
                "queue",
                (f"{_GROUP}/queue/{{booking_id}}",),
            ),
            Screen(
                "group.closure",
                "admin.tab.closure",
                f"{_GROUP}/closure",
                Permission.CLOSURE_MANAGE,
                "alert",
                (
                    f"{_GROUP}/closure/bookings/{{booking_id}}/move",
                    f"{_GROUP}/closure/contacts",
                    f"{_GROUP}/closure/scenarios",
                    f"{_GROUP}/closure/scenarios/{{scenario_id}}",
                    f"{_GROUP}/closure/scenarios/{{scenario_id}}/contacts",
                ),
            ),
        ),
    ),
)
"""Object pages with tabs. The group still has ten tabs until ADR 0047 build step 4."""


def screens() -> Iterator[tuple[str, Screen]]:
    """Yield every screen of the register with the key of its side-navigation entry."""
    for section in SECTIONS:
        for entry in section.entries:
            yield entry.key, entry
    for link in TOP_BAR:
        yield link.key, link
    for obj in OBJECTS:
        for tab in obj.tabs:
            yield obj.entry, tab


def _homes() -> dict[str, Home]:
    """Return the home of every route template the register names."""
    homes: dict[str, Home] = {}
    tab_keys = {tab.key for obj in OBJECTS for tab in obj.tabs}
    for entry, screen in screens():
        tab = screen.key if screen.key in tab_keys else None
        for path in screen.routes:
            homes[path] = Home(entry, tab)
    return homes


_HOMES = _homes()


def home_of(route_path: str) -> Home | None:
    """Return where the route template ``route_path`` lives, or ``None`` if it has no home."""
    return _HOMES.get(route_path)


def object_page(key: str) -> ObjectPage:
    """Return the object page called ``key``."""
    return next(obj for obj in OBJECTS if obj.key == key)


def _rows(translator: Translator, locale: str, items: tuple[Screen, ...]) -> list[str]:
    """Return Markdown table rows for ``items``."""
    return [
        f"| {translator.gettext(locale, s.label_key)} | `{s.path}` | `{s.permission.value}` | "
        + (
            f"shortcut to `{s.target}`"
            if s.target
            else ", ".join(f"`{p}`" for p in s.subpages) or "–"
        )
        + " |"
        for s in items
    ]


def render_markdown(translator: Translator, locale: str = "en") -> str:
    """Return ``docs/admin-map.md``: the side navigation, the top bar and the object pages."""
    header = ["| Entry | Path | Permission | Also the home of |", "|---|---|---|---|"]
    lines = [
        "# Admin structure",
        "",
        "Generated by `python -m app.cli admin-map` from `app/web/navigation.py` (ADR 0047). Do",
        "not edit by hand: a test fails when this file differs from the code. Which entries an",
        "admin sees follows from the authorisation matrix (`docs/authorization.md`).",
        "",
        "## Side navigation",
    ]
    for section in SECTIONS:
        title = translator.gettext(locale, section.label_key)
        lines += ["", f"### {title}", "", *header, *_rows(translator, locale, section.entries)]
    lines += ["", "## Top bar", "", *header, *_rows(translator, locale, TOP_BAR)]
    lines += ["", "## Object pages"]
    entries = {e.key: e for section in SECTIONS for e in section.entries}
    for obj in OBJECTS:
        name = translator.gettext(locale, obj.label_key)
        under = translator.gettext(locale, entries[obj.entry].label_key)
        lines += [
            "",
            f"### {name} (`{obj.path}`)",
            "",
            f"Side navigation: *{under}*.",
            "",
            *header,
            *_rows(translator, locale, obj.tabs),
        ]
    return "\n".join(lines) + "\n"
