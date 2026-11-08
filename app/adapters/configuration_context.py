"""The rules' view of a running installation, for the configuration document (ADR 0099).

A document is checked against the portal's own rules, and several of them depend on the
installation: whether it is production, which locales it has, the limits it is configured
with, the login values of the built-in roles. ``rule_context`` reads them from the settings and
``locked_settings`` from the environment, so no check ever runs on a default that happens to be
permissive.
"""

import os

from app.adapters.settings_report import locked_keys
from app.config import Settings
from app.domain.configuration_changes import RuleContext

LOCALES = frozenset({"nl", "en"})
"""The locales the application has a catalogue for."""


def rule_context(settings: Settings) -> RuleContext:
    """Return the context the rules of a document run in, from the process settings."""
    return RuleContext(
        locales=LOCALES,
        production=settings.environment == "prod",
        settings={
            "embedding_max_origins": settings.embedding_max_origins,
            "max_font_unpacked_bytes": settings.max_font_unpacked_bytes,
            "idp_max_per_organisation": settings.idp_max_per_organisation,
            "booking_form_max_custom_fields": settings.booking_form_max_custom_fields,
            "bundled_idp_login_suffix": settings.bundled_idp_login_suffix,
        },
        built_in_claim_values=frozenset(
            value.casefold()
            for value in (
                settings.oidc_role_superuser,
                settings.oidc_role_org_admin,
                settings.oidc_role_group_admin,
                settings.oidc_role_auditor,
                settings.oidc_role_agent,
            )
        ),
        smtp_plaintext_relay_allowed=settings.smtp_plaintext_relay_allowed,
    )


def locked_settings() -> frozenset[str]:
    """Return the settings the environment locks, by registry key (ADR 0042)."""
    return locked_keys(os.environ)
