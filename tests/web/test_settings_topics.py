"""Every portal setting's topic is declared on a screen of its scope (ADR 0047 §5, §7.3)."""

import pytest

from app.domain.settings_registry import REGISTRY, SettingClass, SettingSpec, Topic
from app.i18n import Translator, load_catalogues
from app.web.navigation import render_markdown, screens, topic_anchor, topic_home
from tests.admin.conftest import SUPER, Site

PORTAL = [spec for spec in REGISTRY if spec.in_portal]

SHOWN_APART = frozenset({"tls_certificate_source"})
"""Portal settings with a block of their own instead of a row in their topic (ADR 0070)."""


@pytest.mark.parametrize("spec", PORTAL, ids=lambda spec: spec.key)
def test_a_portal_setting_has_a_home_of_its_scope(spec: SettingSpec) -> None:
    """Class C on a platform screen, class D on an organisation screen; never without one."""
    home = topic_home(spec)
    prefix = "platform." if spec.class_ is SettingClass.PLATFORM else "organisation."

    assert home is not None, f"declare topic {spec.topic.value} on a settings screen"
    assert home.key.startswith(prefix)


def test_settings_outside_the_portal_have_no_home() -> None:
    outside = [spec for spec in REGISTRY if not spec.in_portal]

    assert outside
    assert all(topic_home(spec) is None for spec in outside)


def test_a_screen_declares_only_topics_with_settings_and_each_once() -> None:
    used = {spec.topic for spec in PORTAL}
    for _, item in screens():
        assert len(set(item.topics)) == len(item.topics), item.key
        assert set(item.topics) <= used, item.key


def test_every_topic_has_a_stable_anchor() -> None:
    assert topic_anchor(Topic.PLANNER) == "#topic-planner"


def test_the_admin_map_lists_the_topics_and_their_screens() -> None:
    text = render_markdown(Translator(load_catalogues()))

    assert "## Settings topics" in text
    assert "`/admin/settings/values#topic-operations`" in text
    assert "`/admin/organisations/{org}/settings#topic-planner`" in text


async def test_each_screen_renders_the_sections_of_its_settings(site: Site) -> None:
    """The section a setting's topic resolves to exists on the screen that holds it."""
    await site.login_as(SUPER)
    pages = {
        "platform.settings.values": (await site.client.get("/admin/settings/values")).text,
        "organisation.settings": (await site.client.get("/admin/organisations/demo/settings")).text,
    }

    for spec in PORTAL:
        if spec.key in SHOWN_APART:
            continue
        home = topic_home(spec)
        assert home is not None
        assert f'id="{topic_anchor(spec.topic)[1:]}"' in pages[home.key], spec.key
