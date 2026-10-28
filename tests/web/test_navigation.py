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
    home_of,
    render_markdown,
    screens,
)
from tests.support.routes import all_routes

DOC = Path(__file__).resolve().parents[2] / "docs" / "admin-map.md"

OVER_TAB_LIMIT = frozenset({"group"})
"""Objects that still have more than ``MAX_TABS`` tabs. Only shrinks (ADR 0047 build step 4)."""


def _page_routes(settings: Settings) -> set[str]:
    """Return every admin GET route that renders a portal page."""
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


def test_home_of_finds_entries_tabs_and_sub_pages() -> None:
    overview = home_of("/admin/")
    staffing_rule = home_of("/admin/groups/{slug}/staffing/{rule_id}")
    help_topic = home_of("/admin/help/{topic}")

    assert overview is not None
    assert overview.entry == "monitor.overview"
    assert overview.tab is None
    assert staffing_rule is not None
    assert staffing_rule.entry == "monitor.overview"
    assert staffing_rule.tab == "group.staffing"
    assert help_topic is not None
    assert help_topic.entry == TOP_BAR[0].key
    assert home_of("/admin/does-not-exist") is None


def test_published_admin_map_matches_the_code() -> None:
    generated = render_markdown(Translator(load_catalogues()))

    assert DOC.read_text() == generated, (
        "docs/admin-map.md is out of date: run `python -m app.cli admin-map > docs/admin-map.md`"
    )
