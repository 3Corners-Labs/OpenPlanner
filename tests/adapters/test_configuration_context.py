"""The rules' view of a running installation (ADR 0099)."""

import pytest

from app.adapters.configuration_context import locked_settings, rule_context
from app.config import Settings


def test_the_context_carries_the_installation_s_limits_roles_and_flags() -> None:
    settings = Settings(
        environment="dev",
        oidc_role_superuser="Super-Beheer",
        smtp_plaintext_relay_allowed=True,
    ).model_copy(update={"environment": "prod"})

    context = rule_context(settings)

    assert context.production
    assert context.smtp_plaintext_relay_allowed
    assert "super-beheer" in context.built_in_claim_values
    assert context.locales == {"nl", "en"}
    assert context.settings["idp_max_per_organisation"] == settings.idp_max_per_organisation
    assert set(context.settings) == {
        "embedding_max_origins",
        "max_font_unpacked_bytes",
        "idp_max_per_organisation",
        "booking_form_max_custom_fields",
        "bundled_idp_login_suffix",
    }


def test_the_cross_field_rule_of_the_settings_is_wired() -> None:
    context = rule_context(Settings(environment="dev"))

    assert context.combination_problem is not None
    assert context.combination_problem({}) is None


def test_the_development_context_is_not_production() -> None:
    assert not rule_context(Settings(environment="dev")).production


def test_the_locked_settings_are_the_registry_keys_the_environment_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURATION_PACKAGE_MAX_MB", "10")

    assert "configuration_package_max_mb" in locked_settings()
