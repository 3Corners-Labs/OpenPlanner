"""Every portal setting's topic has a home in the register on its scope (ADR 0047 §5, §7.3)."""

import pytest

from app.domain.settings_registry import REGISTRY, SettingClass, Topic
from app.i18n import Translator, load_catalogues
from app.web.navigation import (
    OBJECTS,
    SECTIONS,
    render_markdown,
    settings_home,
    topic_anchor,
)
from tests.admin.conftest import SUPER, Site

PORTAL = [spec for spec in REGISTRY if spec.in_portal]


def _platform_keys() -> set[str]:
    platform = next(section for section in SECTIONS if section.key == "platform")
    return {s.key for e in platform.entries for s in (e, *e.subtabs)}


def _organisation_keys() -> set[str]:
    organisation = next(obj for obj in OBJECTS if obj.key == "organisation")
    return {s.key for t in organisation.tabs for s in (t, *t.subtabs)}


@pytest.mark.parametrize("spec", PORTAL, ids=lambda spec: spec.key)
def test_a_portal_setting_lives_on_a_screen_of_its_scope(spec: object) -> None:
    """Class C → a platform screen, class D → an organisation screen; never elsewhere."""
    home = settings_home(spec)  # type: ignore[arg-type]
    scope = (
        _platform_keys()
        if spec.class_ is SettingClass.PLATFORM  # type: ignore[attr-defined]
        else _organisation_keys()
    )

    assert home.key in scope


def test_settings_outside_the_portal_have_no_home() -> None:
    outside = [spec for spec in REGISTRY if not spec.in_portal]

    assert outside
    assert all(settings_home(spec) is None for spec in outside)


def test_every_topic_has_a_stable_anchor() -> None:
    assert topic_anchor(Topic.PLANNER) == "#topic-planner"


def test_the_admin_map_lists_the_topics_and_their_screens() -> None:
    text = render_markdown(Translator(load_catalogues()))

    assert "## Settings topics" in text
    assert "`/admin/settings/values#topic-operations`" in text
    assert "`/admin/organisations/{org}/settings#topic-planner`" in text


async def test_each_screen_shows_the_sections_of_its_topics(site: Site) -> None:
    """The anchor a topic resolves to exists on the screen that holds it."""
    await site.login_as(SUPER)
    platform = (await site.client.get("/admin/settings/values")).text
    organisation = (await site.client.get("/admin/organisations/demo/settings")).text

    for spec in PORTAL:
        page = platform if spec.class_ is SettingClass.PLATFORM else organisation
        assert f'id="{topic_anchor(spec.topic)[1:]}"' in page, spec.key
