"""Object tabs follow the matrix, also for a tab that only holds sub-tabs (ADR 0105)."""

from types import SimpleNamespace

from app.admin.group_routes import OWN_QUEUE_TABS
from app.admin.pages import object_tabs
from app.domain.authorization import Permission
from app.web.navigation import Home, home_of, object_page

BRANDING = "/admin/organisations/demo/branding"
CHANNELS = "/admin/organisations/demo/channels"
TEXTS = "/admin/organisations/demo/texts"
LOCALE = SimpleNamespace(gettext=lambda key: key)


def _tabs(granted: set[Permission], home: Home | None) -> tuple[dict[str, str], list[str]]:
    tabs, row = object_tabs(
        LOCALE,  # type: ignore[arg-type]
        object_page("organisation"),
        "demo",
        lambda screen: screen.permission in granted,
        home,
    )
    return {t.label: t.href for t in tabs}, [r.label for r in row]


ALL = {
    Permission.ORGANISATION_VIEW,
    Permission.BRANDING_MANAGE,
    Permission.INTEGRATIONS_MANAGE,
    Permission.TEXTS_ORGANISATION_MANAGE,
}


def test_the_participants_tab_leads_with_the_branding_page() -> None:
    tabs, row = _tabs(ALL, home_of("/admin/organisations/{org}/branding"))

    assert tabs["admin.tab.participants"] == BRANDING
    assert row == ["admin.tab.branding", "admin.tab.channels", "admin.tab.texts"]


def test_without_branding_the_tab_opens_at_the_first_page_the_admin_may_see() -> None:
    granted = ALL - {Permission.BRANDING_MANAGE}

    tabs, row = _tabs(granted, home_of("/admin/organisations/{org}/channels"))

    assert tabs["admin.tab.participants"] == CHANNELS  # nobody loses the way to Channels
    assert row == ["admin.tab.channels", "admin.tab.texts"]


def test_a_single_visible_page_has_a_tab_but_no_second_row() -> None:
    granted = {Permission.ORGANISATION_VIEW, Permission.TEXTS_ORGANISATION_MANAGE}

    tabs, row = _tabs(granted, home_of("/admin/organisations/{org}/texts"))

    assert tabs["admin.tab.participants"] == TEXTS
    assert row == []


def test_without_any_of_its_pages_there_is_no_participants_tab() -> None:
    tabs, _ = _tabs({Permission.ORGANISATION_VIEW}, None)

    assert "admin.tab.participants" not in tabs


def test_every_own_queue_key_is_a_screen_of_the_group() -> None:
    """``OWN_QUEUE_TABS`` names register keys; a renamed key must not show the queue to all."""
    group = object_page("group")
    keys = {s.key for tab in group.tabs for s in (tab, *tab.subtabs)}

    assert keys >= OWN_QUEUE_TABS


def test_a_hidden_sub_tab_leaves_its_tab_without_a_second_row() -> None:
    """ADR 0047 step 4: on a group without the own queue, *Agenda* has no *Queue* row."""
    granted = {Permission.BOOKINGS_VIEW, Permission.QUEUE_WORK}

    def row(shows_queue: bool) -> list[str]:
        _, sub = object_tabs(
            LOCALE,  # type: ignore[arg-type]
            object_page("group"),
            "demo",
            lambda s: s.permission in granted and (shows_queue or s.key not in OWN_QUEUE_TABS),
            home_of("/admin/groups/{slug}/agenda"),
        )
        return [r.label for r in sub]

    assert row(True) == ["admin.tab.agenda", "admin.tab.queue"]
    assert row(False) == []
