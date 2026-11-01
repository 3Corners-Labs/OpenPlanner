"""Configuration packages (ADR 0089): what an installation's configuration is, and its rules.

A package is a copy of the database with every *state* table emptied and the secrets sealed
again under a passphrase. This module is the one place that says which table is which, what
a passphrase must be and which environment values travel with a package. The SQLite work is
the adapter's (``app/adapters/db/configuration_package``); the rules of an export and an
import are ``ConfigurationPackageService`` (``app.domain.configuration_service``).
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.authorization import Permission
from app.domain.errors import DomainError, ErrorCode

FORMAT = 1
"""The package format; a package of another format is refused."""

MIN_PASSPHRASE_LENGTH = 16
"""A passphrase protects every secret of the platform: a protection, never a setting."""

MEDIA_TYPE = "application/vnd.sqlite3"
"""The media type of a package: a SQLite file."""

PACKAGE_FILENAME = "openplanner-configuration-{installation}-{date}.sqlite"
"""The download name of an exported package, in the portal and over the API."""


def package_filename(installation: str, exported_at: datetime) -> str:
    """Return the download name of a package exported on ``exported_at``."""
    return PACKAGE_FILENAME.format(
        installation=installation or "openplanner", date=exported_at.strftime("%Y%m%d")
    )


VERSION_TABLE = "alembic_version"
"""The schema revision, kept in the package so it can be checked (and later migrated)."""

CONFIGURATION_TABLES = frozenset(
    {
        "api_client_groups",
        "api_clients",
        "api_settings",
        "channel_settings",
        "closure_images",
        "closure_scenarios",
        "group_contacts",
        "group_form_fields",
        "group_holiday_calendars",
        "group_holiday_open_days",
        "group_texts",
        "groups",
        "holiday_calendars",
        "holidays",
        "identity_provider_keys",
        "identity_providers",
        "integration_credentials",
        "organisation_contacts",
        "organisation_fonts",
        "organisation_holiday_calendars",
        "organisation_logos",
        "organisation_origins",
        "organisation_settings",
        "organisation_texts",
        "organisations",
        "platform_settings",
        "reminder_rules",
        "roles",
        "schedule_overrides",
        "schedule_rules",
        "settings_version",
    }
)
"""The tables a package carries: everything an admin configured, nothing that happened."""

STATE_TABLES = frozenset(
    {
        "api_access_tokens",
        "audit_events",
        "booking_events",
        "bookings",
        "call_status",
        "certificate_checks",
        "closure_notices",
        "closures",
        "diagnostic_modes",
        "integration_reports",
        "jobs",
        "maintenance_state",
        "own_certificates",
        "release_checks",
        "release_listing",
        "running_versions",
        "shortfall_acceptances",
        "slots",
        "staff",
        "staff_memberships",
        "staff_refused_entitlements",
        "update_requests",
        "webhook_events",
    }
)
"""The tables a package empties: bookings and their personal data, the log, staff, and what
belongs to this host. A test fails when a table of the model is in neither set."""

IN_USE_TABLES = ("bookings", "slots", "closures")
"""An installation with a row in any of these is in use: an import is refused (point 5)."""

PORTABLE_ENVIRONMENT = (
    "oidc_discovery_url",
    "oidc_client_id",
    "oidc_client_secret",
    "oidc_scopes",
)
"""The class A values that travel with a package (point 4): the login, nothing of the host."""

PERMISSION = Permission.SETTINGS_PLATFORM_MANAGE
"""Export and import are a change of the platform's configuration (point 6)."""

EXPORT_DETAIL = "configuration.export"
IMPORT_DETAIL = "configuration.import"
"""The audit detail of an export and an import; the import adds its counts."""

COUNTED_TABLES = ("organisations", "groups", "identity_providers", "api_clients")
"""What a summary counts, in this order; the import's audit detail names the same."""


@dataclass(frozen=True)
class PackageSummary:
    """What a package holds, as the screen, the CLI and the audit log say it.

    Attributes:
        format: The package format.
        created_at: When it was exported.
        installation: ``INSTALLATION_NAME`` of the source, or empty.
        app_version: The source's release, or empty in development.
        revision: The schema revision of the package.
        counts: Rows per counted table (``COUNTED_TABLES``).
        secrets: How many sealed values the package carries.
        environment: The portable environment variables it carries, upper case.
    """

    format: int
    created_at: datetime
    installation: str
    app_version: str
    revision: str
    counts: dict[str, int] = field(default_factory=dict)
    secrets: int = 0
    environment: tuple[str, ...] = ()

    @property
    def detail(self) -> str:
        """The audit detail of an import: the counts as machine keys, never a value."""
        counts = ",".join(f"{name}:{self.counts.get(name, 0)}" for name in COUNTED_TABLES)
        return f"{IMPORT_DETAIL},{counts},secrets:{self.secrets}"


def check_passphrase(passphrase: str) -> None:
    """Refuse a passphrase shorter than ``MIN_PASSPHRASE_LENGTH``.

    Raises:
        DomainError: ``configuration_passphrase_short``.
    """
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise DomainError(ErrorCode.CONFIGURATION_PASSPHRASE_SHORT, "passphrase")
