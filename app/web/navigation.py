"""The navigation register of the admin portal: where every screen lives (ADR 0047).

One declaration of the portal's structure: sections of the side navigation with their entries,
the top bar, and object pages with their tabs and sub-tabs. Every admin page route belongs to
exactly one entry, tab or sub-tab, as its main path or as a sub-page (a detail or edit page).
The side navigation, the object tabs and ``docs/admin-map.md`` (``python -m app.cli
admin-map``) are rendered from it, and a test fails when an admin page has no home here.

The register decides *where* a screen lives, never *who* may open it: visibility comes only
from the authorisation matrix (ADR 0014), through each screen's permission.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from app.domain.authorization import Permission
from app.domain.settings_registry import SettingClass, SettingSpec, Topic
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


class Width(StrEnum):
    """How wide a page may grow beside the side navigation (ADR 0080).

    The value is what ``<main data-width>`` carries and what ``admin.css`` keys on, so the one
    place that decides a page's width is the register; a template or a stylesheet never does.
    """

    STANDARD = "standard"
    """Reading width, for forms, settings, details and text. The default."""

    WIDE = "wide"
    """Data views (overviews, lists, schedules, the agenda) that use the room they get."""


@dataclass(frozen=True)
class Screen:
    """One side-navigation entry, top-bar link, tab or sub-tab.

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
        width: The page width of ``path`` itself (ADR 0080). A sub-page is a detail or edit page
            that is read or filled in, so it keeps the standard width unless it is listed in
            ``wide_subpages``.
        wide_subpages: The sub-pages that are data views too (for example the week agenda).
        subtabs: For a tab or a side-navigation entry: related screens shown as a second
            row under it (ADR 0047 §3, ADR 0101). The screen's own page is the first item of
            that row.
        own_label_key: For a screen with sub-tabs: the label of its own page in that row,
            when it differs from the screen's label (an entry's name is not its first part).
        topics: For a settings screen: the ADR 0042 topics it shows, as sections in this
            order (ADR 0047 §5, step 6). A topic that exists on two scopes is declared on the
            screen of each, under the same name.
    """

    key: str
    label_key: str
    path: str
    permission: Permission
    icon: str
    subpages: tuple[str, ...] = ()
    target: str = ""
    widget: str = ""
    width: Width = Width.STANDARD
    wide_subpages: tuple[str, ...] = ()
    subtabs: tuple["Screen", ...] = ()
    own_label_key: str = ""
    topics: tuple[Topic, ...] = ()

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
    """The pages of one object (a group or an organisation), shown as tabs.

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
    """Where a route lives: the side-navigation entry, the tab and sub-tab, and the width.

    Attributes:
        entry: Key of the side-navigation entry.
        tab: Key of the tab, on an object page.
        subtab: Key of the sub-tab, when the route lives in a tab's second row.
        width: The page width of the route (ADR 0080).
    """

    entry: str
    tab: str | None
    subtab: str | None = None
    width: Width = Width.STANDARD


PLATFORM_TOPICS = (
    Topic.OPERATIONS,
    Topic.RETENTION,
    Topic.STORAGE,
    Topic.RATE_LIMITS,
    Topic.ADMIN_SESSION,
    Topic.LOGIN_MAPPING,
    Topic.MAIL,
    Topic.WEBEX,
    Topic.UPDATES,
    Topic.DISPLAY,
    Topic.STAFF,
    Topic.PLANNER,
    Topic.BOOKING_FORM,
    Topic.CONFIRMATION,
    Topic.PARTICIPANT_LINK,
)
"""The topics of *Platform › Advanced settings › Settings*, in the order of its sections."""

ORGANISATION_TOPICS = (
    Topic.RETENTION,
    Topic.DISPLAY,
    Topic.STAFF,
    Topic.PLANNER,
    Topic.BOOKING_FORM,
    Topic.CONFIRMATION,
)
"""The topics of an organisation's *Overview › Settings*, in the order of its sections."""

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
                # ADR 0096: the admin arranges their own overview on two sub-pages.
                ("/admin/overview/add", "/admin/overview/edit"),
                width=Width.WIDE,
            ),
            Screen(
                "monitor.audit",
                "admin.nav.audit",
                "/admin/audit",
                Permission.AUDIT_VIEW,
                "audit",
                width=Width.WIDE,
            ),
            Screen(
                "monitor.retention",
                "admin.nav.retention",
                "/admin/retention",
                Permission.RETENTION_VIEW,
                "clock",
            ),
            # ADR 0117 point 11: the cross-object list of how the groups in scope are used; each
            # row links to the group's own *Usage* sub-tab. No edit forms (ADR 0047).
            Screen(
                "monitor.reports",
                "admin.nav.reports",
                "/admin/reports",
                Permission.REPORTS_VIEW,
                "analytics",
                width=Width.WIDE,
            ),
        ),
    ),
    Section(
        "manage",
        "admin.nav.section.manage",
        (
            # ADR 0047 step 3: the organisation as an object page; its tabs are in OBJECTS.
            Screen(
                "manage.organisations",
                "admin.nav.organisations",
                "/admin/organisations",
                Permission.ORGANISATION_VIEW,
                "organisation",
                ("/admin/organisations/new",),
                width=Width.WIDE,
            ),
            # ADR 0047 §2 and step 5: the groups as a list; the group's pages live under it.
            Screen(
                "manage.groups",
                "admin.nav.groups",
                "/admin/groups",
                Permission.GROUP_VIEW,
                "users",
                ("/admin/groups/new",),
                width=Width.WIDE,
            ),
            Screen(
                "manage.staff",
                "admin.nav.staff",
                "/admin/staff",
                Permission.STAFF_VIEW,
                "people",
                ("/admin/staff/invite", "/admin/staff/{member_id}"),
                width=Width.WIDE,
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
                # ADR 0098 point 9a: the admin's own sessions; personal, so next to the
                # own rights and for every admin, without a permission of its own.
                subtabs=(
                    Screen(
                        "manage.rights.sessions",
                        "admin.nav.my_sessions",
                        "/admin/rights/sessions",
                        Permission.RIGHTS_VIEW,
                        "lock",
                    ),
                    # ADR 0123 step 5d: the own screen name, the name a participant sees.
                    Screen(
                        "manage.rights.profile",
                        "admin.nav.my_profile",
                        "/admin/rights/profile",
                        Permission.RIGHTS_VIEW,
                        "people",
                    ),
                ),
            ),
            Screen(
                "manage.matrix",
                "admin.nav.matrix",
                "/admin/matrix",
                Permission.MATRIX_VIEW,
                "matrix",
                # The role editor opens from the matrix (ADR 0039 step 3).
                ("/admin/roles", "/admin/roles/new", "/admin/roles/{role_id}"),
                width=Width.WIDE,
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
            ),
            # ADR 0085: where copies of the log go; one setting for the whole installation.
            Screen(
                "platform.logging",
                "admin.nav.logging",
                "/admin/logging",
                Permission.LOGGING_VIEW,
                "reports",
                ("/admin/logging/new", "/admin/logging/{destination_id}"),
            ),
            Screen(
                "platform.api",
                "admin.nav.api",
                "/admin/api",
                Permission.API_CLIENTS_MANAGE,
                "key",
                ("/admin/api/clients/new", "/admin/api/clients/{client_id}"),
            ),
            # ADR 0047 §4: the shared calendars stay under Platform; an organisation chooses
            # its calendars on its own Holidays tab.
            Screen(
                "platform.holidays",
                "admin.nav.holidays",
                "/admin/holidays",
                Permission.HOLIDAYS_MANAGE,
                "holiday",
                ("/admin/holidays/{calendar}",),
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
            # ADR 0101: one sub-tab per task, System status being the entry's own page.
            Screen(
                "platform.settings",
                "admin.nav.settings",
                "/admin/settings",
                Permission.SETTINGS_PLATFORM_VIEW,
                "sliders",
                own_label_key="admin.settings.tab.status",
                subtabs=(
                    Screen(
                        "platform.settings.values",
                        "admin.settings.tab.values",
                        "/admin/settings/values",
                        Permission.SETTINGS_PLATFORM_VIEW,
                        "sliders",
                        # Class C, and the platform defaults of class D (ADR 0042 §1).
                        topics=PLATFORM_TOPICS,
                    ),
                    Screen(
                        "platform.package",
                        "admin.settings.tab.package",
                        "/admin/settings/package",
                        Permission.SETTINGS_PLATFORM_MANAGE,
                        "export",
                    ),
                ),
            ),
        ),
    ),
)
"""The side navigation, in order."""

TOP_BAR = (
    # Shortcuts (ADR 0047 stays whole: they own no route). Status opens System status; tasks
    # and alerts the overview widgets of the same name (ADR 0061 §7).
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
        "/admin/#widget-alerts",
        Permission.DASHBOARD_VIEW,
        "bell",
        target="monitor.overview",
        widget="alerts",
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
_ORGANISATION = "/admin/organisations/{org}"

OBJECTS = (
    ObjectPage(
        "organisation",
        "admin.nav.object.organisation",
        _ORGANISATION,
        "manage.organisations",
        (
            # The overview: the organisation's groups and profile. Its second row holds the
            # organisation-wide settings of ADR 0042 step 5, ADR 0074 and ADR 0035; the texts for
            # participants moved to *Participants* (ADR 0105).
            Screen(
                "organisation.overview",
                "admin.tab.overview",
                _ORGANISATION,
                Permission.ORGANISATION_VIEW,
                "dashboard",
                # ADR 0084: deleting the organisation is a sub-page of its edit form.
                (f"{_ORGANISATION}/edit", f"{_ORGANISATION}/delete"),
                width=Width.WIDE,
                subtabs=(
                    Screen(
                        "organisation.settings",
                        "admin.tab.settings",
                        f"{_ORGANISATION}/settings",
                        Permission.SETTINGS_ORGANISATION_MANAGE,
                        "sliders",
                        topics=ORGANISATION_TOPICS,
                    ),
                    Screen(
                        "organisation.embedding",
                        "admin.tab.embedding",
                        f"{_ORGANISATION}/embedding",
                        Permission.EMBEDDING_MANAGE,
                        "code",
                    ),
                    # ADR 0121 point 11 (decision S2): the skill catalogue and the capacity pools
                    # are lists with forms of their own, so they cannot be topics of the settings
                    # page (anchors on one form); both live on one sub-tab, like Embedding. It is
                    # read under ``organisation.view``; the forms and links appear for
                    # ``skills.manage`` and ``capacity.manage``, which each write route declares.
                    Screen(
                        "organisation.skills_capacity",
                        "admin.tab.skills_capacity",
                        f"{_ORGANISATION}/skills",
                        Permission.ORGANISATION_VIEW,
                        "tasks",
                        (
                            f"{_ORGANISATION}/skills/{{skill_id}}",
                            f"{_ORGANISATION}/skills/{{skill_id}}/archive",
                            f"{_ORGANISATION}/capacity/{{pool_id}}",
                            f"{_ORGANISATION}/capacity/{{pool_id}}/archive",
                            # ADR 0122 step 4: the staffing of a counted pool hangs under the
                            # pool's own page: a week of blocks, its exceptions, the time zone.
                            f"{_ORGANISATION}/capacity/{{pool_id}}/staffing",
                            f"{_ORGANISATION}/capacity/{{pool_id}}/staffing/{{rule_id}}",
                            f"{_ORGANISATION}/capacity/{{pool_id}}/staffing/{{rule_id}}/split",
                            f"{_ORGANISATION}/capacity/{{pool_id}}/exceptions",
                            f"{_ORGANISATION}/capacity/{{pool_id}}/exceptions/{{override_id}}",
                        ),
                        wide_subpages=(f"{_ORGANISATION}/capacity/{{pool_id}}/staffing",),
                    ),
                    # ADR 0083 step 2: the organisation's own identity providers. A sub-tab
                    # of the overview, like the other organisation-wide settings, so the
                    # organisation page keeps its tabs (ADR 0047 §7).
                    Screen(
                        "organisation.login",
                        "admin.tab.login",
                        f"{_ORGANISATION}/login",
                        Permission.IDENTITY_PROVIDERS_VIEW,
                        "key",
                        (
                            f"{_ORGANISATION}/login/new",
                            f"{_ORGANISATION}/login/{{idp_key}}",
                        ),
                    ),
                    # ADR 0099: the organisation's configuration as a JSON document, whole.
                    Screen(
                        "organisation.configuration",
                        "admin.tab.configuration",
                        f"{_ORGANISATION}/configuration",
                        Permission.SETTINGS_ORGANISATION_MANAGE,
                        "export",
                    ),
                    # ADR 0117 point 11: how the organisation's groups are used (counts only).
                    Screen(
                        "organisation.usage",
                        "admin.tab.usage",
                        f"{_ORGANISATION}/usage",
                        Permission.REPORTS_VIEW,
                        "analytics",
                        width=Width.WIDE,
                    ),
                ),
            ),
            # ADR 0105: what participants see and receive. The tab's own page is the branding;
            # the tab shows for anyone who may open one of its pages (see ``object_tabs``).
            Screen(
                "organisation.participants",
                "admin.tab.participants",
                f"{_ORGANISATION}/branding",
                Permission.BRANDING_MANAGE,
                "palette",
                subtabs=(
                    Screen(
                        "organisation.channels",
                        "admin.tab.channels",
                        f"{_ORGANISATION}/channels",
                        Permission.INTEGRATIONS_MANAGE,
                        "mail",
                    ),
                    Screen(
                        "organisation.texts",
                        "admin.tab.texts",
                        f"{_ORGANISATION}/texts",
                        Permission.TEXTS_ORGANISATION_MANAGE,
                        "mail",
                        (f"{_ORGANISATION}/texts/{{text_locale}}",),
                    ),
                ),
                own_label_key="admin.tab.branding",
            ),
            Screen(
                "organisation.contact",
                "admin.tab.contact",
                f"{_ORGANISATION}/contacts",
                Permission.ORGANISATION_CLOSURE,
                "people",
            ),
            Screen(
                "organisation.holidays",
                "admin.tab.holidays",
                f"{_ORGANISATION}/holidays",
                Permission.HOLIDAYS_ASSIGN,
                "holiday",
            ),
            Screen(
                "organisation.closure",
                "admin.tab.closure",
                f"{_ORGANISATION}/closure",
                Permission.ORGANISATION_CLOSURE,
                "alert",
                (
                    f"{_ORGANISATION}/closure/scenarios",
                    f"{_ORGANISATION}/closure/scenarios/{{scenario_id}}",
                    f"{_ORGANISATION}/closure/scenarios/{{scenario_id}}/contacts",
                ),
            ),
        ),
    ),
    ObjectPage(
        "group",
        "admin.nav.object.group",
        _GROUP,
        "manage.groups",
        # ADR 0047 step 4: six tabs, related screens as sub-tabs of one tab.
        (
            Screen(
                "group.overview",
                "admin.tab.overview",
                _GROUP,
                Permission.GROUP_VIEW,
                "dashboard",
                width=Width.WIDE,
                subtabs=(
                    # ADR 0117 point 11: how the group is used (counts only).
                    Screen(
                        "group.usage",
                        "admin.tab.usage",
                        f"{_GROUP}/usage",
                        Permission.REPORTS_VIEW,
                        "analytics",
                        width=Width.WIDE,
                    ),
                ),
            ),
            # The bookings: the day and week agenda, and the own queue's working list (ADR
            # 0043, shown only on a group that dispatches to it).
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
                    f"{_GROUP}/agenda/bookings/{{booking_id}}/propose",
                    f"{_GROUP}/agenda/slot-propose",
                ),
                width=Width.WIDE,
                wide_subpages=(f"{_GROUP}/agenda/week",),
                subtabs=(
                    Screen(
                        "group.queue",
                        "admin.tab.queue",
                        f"{_GROUP}/queue",
                        Permission.QUEUE_WORK,
                        "queue",
                        (
                            f"{_GROUP}/queue/{{booking_id}}",
                            f"{_GROUP}/queue/requests/{{request_id}}",
                        ),
                        width=Width.WIDE,
                    ),
                ),
            ),
            # When the group is open: staffing (the tab's own page), exceptions, holidays.
            Screen(
                "group.planning",
                "admin.tab.planning",
                f"{_GROUP}/staffing",
                Permission.SCHEDULE_MANAGE,
                "clock",
                (f"{_GROUP}/staffing/{{rule_id}}", f"{_GROUP}/staffing/{{rule_id}}/split"),
                width=Width.WIDE,
                own_label_key="admin.tab.hours",
                subtabs=(
                    Screen(
                        "group.exceptions",
                        "admin.tab.exceptions",
                        f"{_GROUP}/exceptions",
                        Permission.SCHEDULE_MANAGE,
                        "calendar",
                        # ADR 0084 step 5: an exception is changed on its own page.
                        (f"{_GROUP}/exceptions/{{override_id}}",),
                    ),
                    Screen(
                        "group.holidays",
                        "admin.tab.holidays",
                        f"{_GROUP}/holidays",
                        Permission.SCHEDULE_MANAGE,
                        "holiday",
                    ),
                ),
            ),
            # What the participant receives: reminders (the tab's own page), the texts, and
            # the contact block shown during a closure (moved here from *Closure*).
            Screen(
                "group.messages",
                "admin.tab.group_messages",
                f"{_GROUP}/reminders",
                Permission.REMINDERS_MANAGE,
                "mail",
                # ADR 0084 step 5: a rule is changed on its own page.
                (f"{_GROUP}/reminders/{{channel}}/{{minutes}}",),
                own_label_key="admin.tab.reminders",
                subtabs=(
                    Screen(
                        "group.texts",
                        "admin.tab.messages",
                        f"{_GROUP}/messages",
                        Permission.TEXTS_MANAGE,
                        "mail",
                        (f"{_GROUP}/messages/{{text_locale}}",),
                    ),
                    Screen(
                        "group.contact",
                        "admin.tab.contact",
                        f"{_GROUP}/contact",
                        Permission.CLOSURE_MANAGE,
                        "people",
                    ),
                ),
            ),
            # The group's settings, with the connection check as a section of the page.
            Screen(
                "group.settings",
                "admin.tab.settings",
                f"{_GROUP}/settings",
                Permission.GROUP_EDIT,
                "settings",
                # ADR 0055: the booking form is a sub-page of the settings; ADR 0084: so is
                # the page that archives the group.
                (
                    f"{_GROUP}/settings/form",
                    f"{_GROUP}/settings/form/{{key}}",
                    # ADR 0081 point 7: subjects and routing, next to the booking form.
                    f"{_GROUP}/settings/routing",
                    f"{_GROUP}/settings/routing/subjects/{{code}}",
                    f"{_GROUP}/settings/archive",
                ),
                subtabs=(
                    # ADR 0121 step 2d: the flows of the service (scope → object → tab →
                    # sub-tab), read and changed under group.edit. A flow is addressed by its
                    # code; the pages with a form are its sub-pages, the POST-only actions
                    # (prepare, publish, activate) have no page of their own.
                    Screen(
                        "group.flows",
                        "admin.tab.flows",
                        f"{_GROUP}/flows",
                        Permission.GROUP_EDIT,
                        "tasks",
                        (
                            f"{_GROUP}/flows/new",
                            f"{_GROUP}/flows/{{code}}",
                            f"{_GROUP}/flows/{{code}}/subjects/{{subject}}",
                            f"{_GROUP}/flows/{{code}}/discard",
                            f"{_GROUP}/flows/{{code}}/archive",
                            f"{_GROUP}/flows/{{code}}/delete",
                        ),
                    ),
                ),
            ),
            Screen(
                "group.closure",
                "admin.tab.closure",
                f"{_GROUP}/closure",
                Permission.CLOSURE_MANAGE,
                "alert",
                (
                    f"{_GROUP}/closure/bookings/{{booking_id}}/move",
                    f"{_GROUP}/closure/scenarios",
                    f"{_GROUP}/closure/scenarios/{{scenario_id}}",
                    f"{_GROUP}/closure/scenarios/{{scenario_id}}/contacts",
                ),
            ),
        ),
    ),
)
"""Object pages with tabs and sub-tabs."""


def _scope_screens(class_: SettingClass) -> Iterator[Screen]:
    """Yield the screens of the scope where a setting of ``class_`` is changed."""
    for entry, item in screens():
        organisation = entry == object_page("organisation").entry and item.key.startswith(
            "organisation."
        )
        if (class_ is SettingClass.ORGANISATION and organisation) or (
            class_ is SettingClass.PLATFORM and item.key.startswith("platform.")
        ):
            yield item


def topic_home(spec: SettingSpec) -> Screen | None:
    """Return the screen of ``spec``'s scope that declares its topic (ADR 0047 §5).

    Class C lives on a platform screen, class D on an organisation screen; ``None`` for a
    setting outside the portal (class A and B) or a topic no screen of its scope declares.
    """
    if not spec.in_portal:
        return None
    return next((s for s in _scope_screens(spec.class_) if spec.topic in s.topics), None)


def topic_anchor(topic: Topic) -> str:
    """Return the fragment of ``topic``'s section on its screen (``#topic-<value>``)."""
    return f"#topic-{topic.value}"


def screens() -> Iterator[tuple[str, Screen]]:
    """Yield every screen of the register with the key of its side-navigation entry."""
    for section in SECTIONS:
        for entry in section.entries:
            yield entry.key, entry
            for subtab in entry.subtabs:
                yield entry.key, subtab
    for link in TOP_BAR:
        yield link.key, link
    for obj in OBJECTS:
        for tab in obj.tabs:
            yield obj.entry, tab
            for subtab in tab.subtabs:
                yield obj.entry, subtab


def _home(entry: str, tab: str | None, subtab: str | None, screen: Screen, path: str) -> Home:
    """Return the home of ``path`` of ``screen``; wide only for its own page or a listed one."""
    wide = (path == screen.path and screen.width is Width.WIDE) or path in screen.wide_subpages
    return Home(entry, tab, subtab, Width.WIDE if wide else Width.STANDARD)


def _homes() -> dict[str, Home]:
    """Return the home of every route template the register names."""
    homes: dict[str, Home] = {}
    for section in SECTIONS:
        for entry in section.entries:
            homes.update({path: _home(entry.key, None, None, entry, path) for path in entry.routes})
            for sub in entry.subtabs:
                homes.update(
                    {path: _home(entry.key, None, sub.key, sub, path) for path in sub.routes}
                )
    for link in TOP_BAR:
        homes.update({path: _home(link.key, None, None, link, path) for path in link.routes})
    for obj in OBJECTS:
        for tab in obj.tabs:
            homes.update({path: _home(obj.entry, tab.key, None, tab, path) for path in tab.routes})
            for sub in tab.subtabs:
                homes.update(
                    {path: _home(obj.entry, tab.key, sub.key, sub, path) for path in sub.routes}
                )
    return homes


_HOMES = _homes()


def home_of(route_path: str) -> Home | None:
    """Return where the route template ``route_path`` lives, or ``None`` if it has no home."""
    return _HOMES.get(route_path)


def screen(key: str) -> Screen:
    """Return the screen of the register called ``key`` (an entry, tab or sub-tab).

    Raises:
        KeyError: When no screen has that key; a caller names keys of the register only.
    """
    found = next((s for _, s in screens() if s.key == key), None)
    if found is None:
        raise KeyError(key)
    return found


def entry(key: str) -> Screen | None:
    """Return the side-navigation entry called ``key``, if any."""
    return next((e for section in SECTIONS for e in section.entries if e.key == key), None)


def object_page(key: str) -> ObjectPage:
    """Return the object page called ``key``."""
    return next(obj for obj in OBJECTS if obj.key == key)


def _rows(translator: Translator, locale: str, items: tuple[Screen, ...]) -> list[str]:
    """Return Markdown table rows for ``items``, each tab followed by its sub-tabs."""
    rows: list[str] = []
    for s in items:
        rows.append(_row(translator, locale, s, translator.gettext(locale, s.label_key)))
        for sub in s.subtabs:
            tab_name = translator.gettext(locale, s.label_key)
            label = f"{tab_name} › {translator.gettext(locale, sub.label_key)}"
            rows.append(_row(translator, locale, sub, label))
    return rows


def _row(translator: Translator, locale: str, s: Screen, label: str) -> str:
    """Return one Markdown table row for ``s`` under ``label``, with its page width."""
    homes = (
        f"shortcut to `{s.target}`" if s.target else ", ".join(f"`{p}`" for p in s.subpages) or "–"
    )
    width = "–" if s.target else s.width.value
    return f"| {label} | `{s.path}` | `{s.permission.value}` | {width} | {homes} |"


def render_markdown(translator: Translator, locale: str = "en") -> str:
    """Return ``docs/admin-map.md``: the side navigation, the top bar and the object pages."""
    header = [
        "| Entry | Path | Permission | Width | Also the home of |",
        "|---|---|---|---|---|",
    ]
    lines = [
        "# Admin structure",
        "",
        "Generated by `python -m app.cli admin-map` from `app/web/navigation.py` (ADR 0047). Do",
        "not edit by hand: a test fails when this file differs from the code. Which entries an",
        "admin sees follows from the authorisation matrix (`docs/authorization.md`). Width is the",
        "page width beside the side navigation (ADR 0080): `wide` for data views, `standard` for",
        "forms and reading; a sub-page is `standard` unless the register lists it as wide.",
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
            f"Side navigation: *{under}*. A row *Tab › Sub-tab* is a sub-tab of that tab.",
            "",
            *header,
            *_rows(translator, locale, obj.tabs),
        ]
    lines += ["", *_topic_rows(translator, locale)]
    return "\n".join(lines) + "\n"


def _topic_rows(translator: Translator, locale: str) -> list[str]:
    """Return the section of ``docs/admin-map.md`` listing each settings topic's homes."""
    rows = [
        "## Settings topics",
        "",
        "A settings screen declares the ADR 0042 topics it shows, as sections in this order",
        "(ADR 0047 §5). A class C setting's topic is declared on a platform screen, a class D",
        "setting's on an organisation screen; a test fails otherwise.",
        "",
        "| Topic | Screen | Section |",
        "|---|---|---|",
    ]
    for _, item in screens():
        name = translator.gettext(locale, item.label_key)
        for topic in item.topics:
            rows.append(f"| `{topic.value}` | {name} | `{item.path}{topic_anchor(topic)}` |")
    return rows
