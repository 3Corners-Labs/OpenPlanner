"""The navigation register (ADR 0047): every admin screen has exactly one home."""

from pathlib import Path

from fastapi.responses import HTMLResponse

from app.admin.routes import PUBLIC_ADMIN_PATHS
from app.config import Settings
from app.i18n import Translator, load_catalogues
from app.main import create_app
from app.web.navigation import (
    MAX_SECTIONS,
    MAX_TABS,
    OBJECTS,
    SECTIONS,
    TOP_BAR,
    Home,
    Screen,
    Width,
    home_of,
    object_page,
    render_markdown,
    screens,
)
from app.web.widgets import widget
from tests.support.routes import all_routes

DOC = Path(__file__).resolve().parents[2] / "docs" / "admin-map.md"

OVER_TAB_LIMIT = frozenset({"group"})
"""Objects that still have more than ``MAX_TABS`` tabs. Only shrinks (ADR 0047 build step 4)."""


def _page_routes(settings: Settings) -> set[str]:
    """Return every admin GET route that renders a portal page.

    A widget fragment (``FragmentResponse``, ADR 0061) is part of the overview, not a page.
    """
    return {
        route.path
        for route in all_routes(create_app(settings).routes)
        if "GET" in route.methods
        and route.path.startswith("/admin")
        and route.path not in PUBLIC_ADMIN_PATHS
        and route.response_class is HTMLResponse
    }


def test_every_admin_page_has_a_home_in_the_register(settings: Settings) -> None:
    homeless = {path for path in _page_routes(settings) if home_of(path) is None}

    assert homeless == set(), (
        "place the new screen in app/web/navigation.py: an existing entry or tab, or a sub-page "
        "of one (ADR 0047); a new section, entry or tab needs an ADR"
    )


def test_the_register_names_only_existing_routes(settings: Settings) -> None:
    declared = {path for _, screen in screens() for path in screen.routes}

    assert declared - _page_routes(settings) == set()


def test_no_route_has_two_homes() -> None:
    declared = [path for _, screen in screens() for path in screen.routes]

    assert len(declared) == len(set(declared))


def test_keys_are_unique() -> None:
    keys = [screen.key for _, screen in screens()]
    keys += [section.key for section in SECTIONS] + [obj.key for obj in OBJECTS]

    assert len(keys) == len(set(keys))


def test_the_structure_stays_within_its_limits() -> None:
    over = {obj.key for obj in OBJECTS if len(obj.tabs) > MAX_TABS}

    assert len(SECTIONS) <= MAX_SECTIONS
    assert all(section.entries for section in SECTIONS)
    assert over == OVER_TAB_LIMIT, "remove an object from OVER_TAB_LIMIT once it fits"


def test_every_object_page_belongs_to_a_side_navigation_entry() -> None:
    entries = {entry.key for section in SECTIONS for entry in section.entries}

    assert all(obj.entry in entries for obj in OBJECTS)


def test_tabs_live_under_their_object() -> None:
    for obj in OBJECTS:
        for tab in obj.tabs:
            assert all(path.startswith(obj.path) for path in tab.routes), tab.key
            for subtab in tab.subtabs:
                assert all(path.startswith(obj.path) for path in subtab.routes), subtab.key


def test_only_tabs_and_entries_have_sub_tabs() -> None:
    """ADR 0099: a tab or a side-navigation entry has sub-tabs; a sub-tab or shortcut never."""
    owners = {tab.key for obj in OBJECTS for tab in obj.tabs}
    owners |= {entry.key for section in SECTIONS for entry in section.entries}

    for entry, screen in screens():
        if screen.key not in owners:
            assert screen.subtabs == (), screen.key
        assert entry


def test_sub_tabs_of_an_entry_live_under_its_path() -> None:
    for section in SECTIONS:
        for entry in section.entries:
            for sub in entry.subtabs:
                assert all(path.startswith(entry.path + "/") for path in sub.routes), sub.key


def test_advanced_settings_has_three_sub_tabs() -> None:
    """ADR 0099 point 2: System status (the entry's own page), Settings, Configuration package."""
    settings = next(e for s in SECTIONS for e in s.entries if e.key == "platform.settings")

    assert [sub.key for sub in settings.subtabs] == ["platform.settings.values", "platform.package"]
    assert home_of("/admin/settings") == Home("platform.settings", None, None)
    assert home_of("/admin/settings/values") == Home(
        "platform.settings", None, "platform.settings.values"
    )
    assert home_of("/admin/settings/package") == Home("platform.settings", None, "platform.package")


def test_the_organisation_is_an_object_page_with_six_tabs() -> None:
    organisation = object_page("organisation")

    assert organisation.entry == "manage.organisations"
    assert [tab.key for tab in organisation.tabs] == [
        "organisation.overview",
        "organisation.branding",
        "organisation.channels",
        "organisation.contact",
        "organisation.holidays",
        "organisation.closure",
    ]
    assert [sub.key for sub in organisation.tabs[0].subtabs] == [
        "organisation.settings",
        "organisation.texts",
        "organisation.embedding",
        "organisation.login",
    ]


def test_groups_and_organisations_are_entries_of_manage() -> None:
    manage = next(section for section in SECTIONS if section.key == "manage")

    assert [entry.key for entry in manage.entries][:2] == ["manage.organisations", "manage.groups"]
    assert object_page("group").entry == "manage.groups"
    # ADR 0047 §4: the per-organisation lists left the menu; the calendars went to Platform.
    keys = {entry.key for section in SECTIONS for entry in section.entries}
    assert keys.isdisjoint(
        {"manage.branding", "manage.embedding", "manage.organisation_settings", "manage.closures"}
    )
    assert "platform.holidays" in keys


def test_home_of_finds_entries_tabs_and_sub_pages() -> None:
    overview = home_of("/admin/")
    staffing_rule = home_of("/admin/groups/{slug}/staffing/{rule_id}")
    texts = home_of("/admin/organisations/{org}/texts/{text_locale}")
    edit = home_of("/admin/organisations/{org}/edit")
    help_topic = home_of("/admin/help/{topic}")

    assert overview is not None
    assert overview.entry == "monitor.overview"
    assert overview.tab is None
    assert staffing_rule is not None
    assert staffing_rule.entry == "manage.groups"
    assert staffing_rule.tab == "group.staffing"
    assert staffing_rule.subtab is None
    assert texts == Home("manage.organisations", "organisation.overview", "organisation.texts")
    assert edit == Home("manage.organisations", "organisation.overview", None)
    assert help_topic is not None
    assert help_topic.entry == "top.help"
    assert home_of("/admin/does-not-exist") is None


def test_published_admin_map_matches_the_code() -> None:
    generated = render_markdown(Translator(load_catalogues()))

    assert DOC.read_text() == generated, (
        "docs/admin-map.md is out of date: run `python -m app.cli admin-map > docs/admin-map.md`"
    )


def _shortcuts() -> list[Screen]:
    return [link for link in TOP_BAR if link.target]


def test_the_top_bar_has_three_shortcuts_before_help() -> None:
    assert [link.key for link in TOP_BAR] == ["top.status", "top.tasks", "top.alerts", "top.help"]
    assert [link.key for link in _shortcuts()] == ["top.status", "top.tasks", "top.alerts"]


def test_a_shortcut_leads_into_an_entry_that_has_a_home() -> None:
    entries = {entry.key: entry for section in SECTIONS for entry in section.entries}

    for link in _shortcuts():
        target = entries[link.target]
        destination = link.path.split("#")[0].split("?")[0]
        assert destination == target.path, link.key


def test_a_shortcut_is_the_home_of_no_route() -> None:
    assert all(link.routes == () for link in _shortcuts())
    assert home_of("/admin/#widget-tasks") is None


def test_a_shortcut_to_a_widget_names_an_available_widget() -> None:
    for link in _shortcuts():
        if link.widget:
            item = widget(link.widget)
            assert item is not None, link.key
            assert item.available, link.key
            assert link.path.endswith(f"#widget-{link.widget}")


def test_a_screen_is_standard_width_unless_it_declares_wide() -> None:
    screen = Screen("x.y", "k", "/admin/x", next(iter(screens()))[1].permission, "dashboard")

    assert screen.width is Width.STANDARD
    assert screen.wide_subpages == ()


def test_home_of_reports_the_page_width_of_a_route() -> None:
    assert home_of("/admin/").width is Width.WIDE  # type: ignore[union-attr]
    assert home_of("/admin/audit").width is Width.WIDE  # type: ignore[union-attr]
    assert home_of("/admin/retention").width is Width.STANDARD  # type: ignore[union-attr]
    assert home_of("/admin/groups/{slug}").width is Width.WIDE  # type: ignore[union-attr]
    assert home_of("/admin/groups/{slug}/agenda/week").width is Width.WIDE  # type: ignore[union-attr]


def test_a_sub_page_is_standard_width_unless_the_screen_lists_it() -> None:
    # A detail or edit page is read or filled in, so it keeps the reading width.
    assert home_of("/admin/groups/{slug}/bookings/{booking_id}").width is Width.STANDARD  # type: ignore[union-attr]
    assert home_of("/admin/groups/{slug}/staffing/{rule_id}").width is Width.STANDARD  # type: ignore[union-attr]
    assert home_of("/admin/groups/{slug}/settings").width is Width.STANDARD  # type: ignore[union-attr]


def test_a_wide_sub_page_is_a_sub_page_of_its_own_screen() -> None:
    for _, screen in screens():
        assert set(screen.wide_subpages) <= set(screen.subpages), screen.key


def test_a_shortcut_declares_no_width() -> None:
    # It owns no route, so a width on it would never apply.
    for link in TOP_BAR:
        if link.target:
            assert link.width is Width.STANDARD
            assert link.wide_subpages == ()


def test_the_admin_map_shows_the_width_of_every_screen() -> None:
    text = render_markdown(Translator(load_catalogues()), "en")

    assert "| Width |" in text
    assert "| wide |" in text
    assert "| standard |" in text


def test_the_lists_and_the_organisation_overview_are_wide_and_the_forms_standard() -> None:
    # ADR 0080: data views use the room they get, forms and settings keep the reading width.
    assert home_of("/admin/groups").width is Width.WIDE  # type: ignore[union-attr]
    assert home_of("/admin/organisations").width is Width.WIDE  # type: ignore[union-attr]
    assert home_of("/admin/organisations/{org}").width is Width.WIDE  # type: ignore[union-attr]
    assert home_of("/admin/groups/new").width is Width.STANDARD  # type: ignore[union-attr]
    assert home_of("/admin/organisations/{org}/edit").width is Width.STANDARD  # type: ignore[union-attr]
    assert home_of("/admin/organisations/{org}/branding").width is Width.STANDARD  # type: ignore[union-attr]
    assert home_of("/admin/organisations/{org}/settings").width is Width.STANDARD  # type: ignore[union-attr]
