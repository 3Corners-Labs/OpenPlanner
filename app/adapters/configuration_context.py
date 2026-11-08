"""The rules' view of a running installation, for the configuration document (ADR 0099).

A document is checked against the portal's own rules, and several of them depend on the
installation: whether it is production, which locales it has, the limits it is configured
with, the login values of the built-in roles. ``rule_context`` reads them from the settings and
``locked_settings`` from the environment, so no check ever runs on a default that happens to be
permissive.
"""

import os
from collections.abc import Callable

from app.adapters.configuration_discovery import issuer_fetcher
from app.adapters.configuration_json import JsonCodec
from app.adapters.configuration_seal import PassphraseSealing
from app.adapters.contact_centers import DESCRIPTORS
from app.adapters.settings_report import combination_problem, locked_keys
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.configuration_changes import RuleContext
from app.domain.configuration_document_service import ConfigurationDocumentService
from app.domain.ports import Clock, DiscoveryFetcher, UnitOfWork

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
        contact_centers={kind.value: item for kind, item in DESCRIPTORS.items()},
        combination_problem=lambda proposed: combination_problem(settings, os.environ, proposed),
    )


def locked_settings() -> frozenset[str]:
    """Return the settings the environment locks, by registry key (ADR 0042)."""
    return locked_keys(os.environ)


def document_service(
    uow: UnitOfWork,
    clock: Clock,
    admin: AdminService,
    *,
    settings: Callable[[], Settings],
    discovery: DiscoveryFetcher,
    locked: Callable[[], frozenset[str]] = locked_settings,
) -> ConfigurationDocumentService:
    """Build the service that exports, plans and applies configuration documents (ADR 0099).

    The one place the portal and the host commands get theirs from, so the two cannot drift.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Writes the audit events of an export.
        settings: The effective settings: the limits and the rules are read on each use (the
            portal's follow the database); the installation name, release and environment when
            the service is built.
        discovery: Fetches the discovery document of a new or moved identity provider.
        locked: The settings the environment locks.
    """
    return ConfigurationDocumentService(
        uow,
        clock,
        admin,
        JsonCodec(),
        PassphraseSealing(),
        installation=settings().installation_name or "",
        app_version=settings().app_version or "",
        context=lambda: rule_context(settings()),
        locked=locked,
        max_bytes=lambda: settings().configuration_package_max_mb * 1024 * 1024,
        max_changes=lambda: settings().configuration_apply_max_changes,
        issuer_of=issuer_fetcher(discovery, production=settings().environment == "prod"),
    )
