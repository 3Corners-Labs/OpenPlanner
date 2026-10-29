"""The settings screens' labels cover the registry and the start-up check (ADR 0042)."""

from app.config import Settings
from app.domain.platform_settings import Source
from app.domain.settings_registry import REGISTRY, SettingClass, Topic
from app.web.settings_labels import (
    CLASS_LABELS,
    PROBLEM_LABELS,
    SETTING_LABELS,
    SOURCE_LABELS,
    TOPIC_LABELS,
)


def test_every_portal_setting_has_its_registry_label() -> None:
    assert SETTING_LABELS == {s.key: s.label_key for s in REGISTRY if s.in_portal}


def test_every_topic_source_and_class_has_a_label() -> None:
    assert set(TOPIC_LABELS) == set(Topic)
    assert set(SOURCE_LABELS) == set(Source)
    assert set(CLASS_LABELS) == set(SettingClass)


def test_every_production_problem_has_a_label() -> None:
    everything_wrong = Settings(
        environment="dev",
        simulator_enabled=True,
        webhook_base_url="http://tunnel.example",
        webex_client_id="client",
        oidc_discovery_url="http://idp.example",
    )

    assert {p.code for p in everything_wrong.production_problems()} == set(PROBLEM_LABELS)
