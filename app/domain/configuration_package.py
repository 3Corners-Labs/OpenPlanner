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

FORMAT = 1
"""The package format; a package of another format is refused."""

MIN_PASSPHRASE_LENGTH = 16
"""A passphrase protects every secret of the platform: a protection, never a setting."""


VERSION_TABLE = "alembic_version"
"""The schema revision, kept in the package so it can be checked (and later migrated)."""

CONFIGURATION_TABLES = frozenset(
    {
        "alert_targets",
        "api_client_groups",
        "api_clients",
        "api_settings",
        "capacity_pool_skills",
        "capacity_pools",
        "channel_settings",
        "closure_images",
        "closure_scenarios",
        "flow_revision_skills",
        "flow_revisions",
        "group_contact_center",
        "group_contacts",
        "group_form_fields",
        "group_holiday_calendars",
        "group_holiday_open_days",
        "group_texts",
        "groups",
        "holiday_calendars",
        "holidays",
        "identity_provider_domains",
        "identity_provider_keys",
        "identity_providers",
        "integration_credentials",
        "log_destinations",
        "organisation_contacts",
        "organisation_fonts",
        "organisation_holiday_calendars",
        "organisation_logos",
        "organisation_origins",
        "organisation_settings",
        "organisation_texts",
        "organisations",
        "platform_settings",
        "pool_staffing_overrides",
        "pool_staffing_rules",
        "reminder_rules",
        "roles",
        "schedule_overrides",
        "schedule_rules",
        "service_flows",
        "settings_version",
        "skills",
        "subject_flow_skills",
        "subject_flows",
    }
)
"""The tables a package carries: everything an admin configured, nothing that happened."""

STATE_TABLES = frozenset(
    {
        "admin_sessions",
        "api_access_tokens",
        "audit_events",
        "booking_day_counts",
        "booking_events",
        "bookings",
        "call_status",
        "certificate_checks",
        "closure_notices",
        "closures",
        "diagnostic_modes",
        "instance",
        "integration_reports",
        "alert_deliveries",
        "jobs",
        "reschedule_proposals",
        "change_requests",
        "log_destination_status",
        "maintenance_state",
        "overview_layouts",
        "own_certificates",
        "participant_link_bookings",
        "participant_link_sessions",
        "public_address_checks",
        "release_checks",
        "release_listing",
        "report_digest_runs",
        "report_subscriptions",
        "request_personal_data",
        "running_versions",
        "service_requests",
        "shortfall_acceptances",
        "shortfall_watches",
        "slots",
        "staff",
        "staff_memberships",
        "staff_skills",  # who holds which skill: per person, like ``staff`` (ADR 0121 2b-ii)
        "wallboard_links",
        "staff_refused_entitlements",
        "update_requests",
        "usage_backfill",
        "usage_events",
        "usage_hour",
        "usage_rollup_state",
        "usage_staff_hour",
        "webhook_events",
        "webhook_secret_sightings",  # this host's first sightings of its webhook secrets (ADR 0124)
        "work_items",  # requests and their work: what happened, like bookings (ADR 0121 step 5)
    }
)
"""The tables a package empties: bookings and their personal data, the log, staff, and what
belongs to this host. A test fails when a table of the model is in neither set."""

PERMISSION = Permission.SETTINGS_PLATFORM_MANAGE
"""Reading a package is a change of the platform's configuration (point 6)."""

IMPORT_DETAIL = "configuration.import"
"""The audit detail of a package import; it adds its counts."""

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
