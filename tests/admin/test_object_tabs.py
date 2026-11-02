"""Object tabs follow the matrix, also for a tab that only holds sub-tabs (ADR 0105)."""

from types import SimpleNamespace

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
        lambda permission: permission in granted,
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
