"""Application settings, read from the environment only.

A bootstrap secret ``X`` may instead come from the file that ``X_FILE`` names (ADR 0042 §9),
so Docker secrets, Kubernetes secrets and secret-store sidecars work without an env file.
"""

import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.domain.event_schema import Envelope
from app.domain.settings_registry import REGISTRY, SettingClass
from app.domain.system_log import EventRetention
from app.i18n.dates import ClockStyle

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

AGENT_API_PREFIX = "/api/agent/v1"
"""Where the machine API for contact-center bots is mounted (ADR 0023)."""

SMTPS_PORT = 465
"""Mail submission over implicit TLS (RFC 8314); STARTTLS is never offered there."""

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def environment() -> dict[str, str]:
    """Return a copy of the process environment, for reporting where a setting came from.

    The only other place that reads the environment; ``settings list`` uses it (ADR 0042 §10).
    """
    return dict(os.environ)


FILE_SECRETS: tuple[str, ...] = tuple(
    spec.key
    for spec in REGISTRY
    if spec.secret and spec.class_ in (SettingClass.BOOTSTRAP, SettingClass.DEPLOYMENT)
)
"""Secrets that ``X_FILE`` may supply: the class A and B secrets of the registry (§9)."""

_PRIVATE_MODE = 0o077
"""Permission bits for group and others; a secret file with any of them set is reported."""


def _lower(environ: dict[str, str]) -> dict[str, str]:
    """Return the environment with lower-case names (pydantic-settings ignores case)."""
    return {name.lower(): value for name, value in environ.items()}


def _read_secret_file(name: str, path: Path) -> str:
    """Return a secret file's contents without the trailing newline; refuse missing or empty.

    Messages name the variable only: neither the path nor the contents reach a log.
    """
    variable = f"{name.upper()}_FILE"
    try:
        value = path.read_text(encoding="utf-8").removesuffix("\n").removesuffix("\r")
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"{variable} names no readable file") from None
    if not value:
        raise ValueError(f"{variable} is empty")
    return value


def loose_secret_files(environ: dict[str, str]) -> list[str]:
    """Return the ``X_FILE`` variables whose file others may read, for a warning at start-up."""
    env = _lower(environ)
    loose: list[str] = []
    for name in FILE_SECRETS:
        path = env.get(f"{name}_file")
        try:
            if path is not None and Path(path).stat().st_mode & _PRIVATE_MODE:
                loose.append(f"{name.upper()}_FILE")
        except OSError:
            continue
    return loose


class _SecretFiles(PydanticBaseSettingsSource):
    """Settings source for ``X_FILE`` (ADR 0042 §9): no ``secrets_dir`` and no implicit names."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Unused: ``__call__`` reads every file secret at once."""
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        """Return the secrets whose ``X_FILE`` is set; refuse ``X`` and ``X_FILE`` together."""
        env = _lower(dict(os.environ))
        values: dict[str, Any] = {}
        for name in FILE_SECRETS:
            path = env.get(f"{name}_file")
            if path is None:
                continue
            if name in env:
                raise ValueError(f"{name.upper()} and {name.upper()}_FILE are both set; set one")
            values[name] = _read_secret_file(name, Path(path))
        return values


class Settings(BaseSettings):
    """Typed configuration. Each field maps to the upper-case environment variable.

    Secrets never get defaults here; they come from the environment or the secret store.
    ``.env.example`` lists every variable with a placeholder.

    Secure by default: ``ENVIRONMENT`` defaults to ``prod``, which requires
    ``MANAGEMENT_TOKEN_KEY`` and an ``https`` ``PUBLIC_BASE_URL``. Development sets
    ``ENVIRONMENT=dev`` explicitly (``.env.example``).
    """

    # Validation error messages never echo the input: it holds the secrets from the environment.
    # ``errors()`` still returns it; callers that inspect errors pass ``include_input=False``.
    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Read ``X_FILE`` next to the environment; arguments still win (tests)."""
        return (init_settings, _SecretFiles(settings_cls), env_settings, dotenv_settings)

    environment: Literal["dev", "test", "prod"] = "prod"
    database_url: str = "sqlite+aiosqlite:///data/booking.db"
    log_level: LogLevel = "INFO"

    # How clock times are written everywhere (admin agenda, booking pages, messages):
    # ``24h`` (default), ``12h`` or ``locale`` (per language). Dates always follow the locale.
    time_format: ClockStyle = ClockStyle.H24

    # Worker loop: poll interval and the heartbeat file its container healthcheck reads.
    worker_poll_seconds: float = 30.0
    worker_heartbeat_path: Path = Path("data/worker.heartbeat")
    worker_heartbeat_max_age_seconds: float = 120.0
    # ADR 0031: system events (job outcomes) in the audit log are deleted after this many
    # days; admin actions are kept.
    system_event_retention_days: int = Field(default=90, ge=1, le=3650)
    # ADR 0040: grades of the data volume. Below these free-space percentages (or above
    # DATABASE_MAX_MB, default no limit) the worker records a system event, logs a warning or
    # error every hour and /readyz answers "degraded".
    storage_low_free_percent: float = Field(default=15.0, gt=0, lt=100)
    storage_critical_free_percent: float = Field(default=5.0, gt=0, lt=100)
    database_max_mb: int | None = Field(default=None, ge=1)
    # ADR 0040 / 0034 (class B, deployment): name of this installation in every log line and
    # event record, so a log platform that receives several can tell them apart. Empty: none.
    installation_name: str = Field(default="", pattern=r"^[A-Za-z0-9_.-]{0,64}$")
    # ADR 0034, AU-4 (class C, platform; env until the settings registry of ADR 0042): rejected
    # webhooks and failing token refreshes are written as at most one security event per kind
    # per this many minutes; the rest are counted into the next one.
    security_event_window_minutes: int = Field(default=15, ge=1, le=1440)
    # ADR 0040, part 3 (class C, platform; env until the settings registry of ADR 0042): one
    # CSV export of the audit log holds at most this many rows and covers at most this many
    # days; a larger one is refused, never cut short.
    audit_export_max_rows: int = Field(default=100_000, ge=1_000, le=1_000_000)
    audit_export_max_days: int = Field(default=366, ge=1, le=3660)
    # ADR 0040, part 4 (class C, platform; env until the settings registry of ADR 0042): how
    # long each category of audit event is kept. Admin actions 2 years (product owner,
    # 2026-10-29), security 1 year, debug events 7 days; system events above. Diagnostic mode
    # ends by itself after DIAGNOSTIC_MODE_HOURS.
    audit_retention_days: int = Field(default=730, ge=365, le=3650)
    security_event_retention_days: int = Field(default=365, ge=30, le=3650)
    diagnostic_event_retention_days: int = Field(default=7, ge=1, le=90)
    diagnostic_mode_hours: int = Field(default=24, ge=1, le=72)

    # ADR 0044: how many days ahead the shortfall summary (group page, dashboard) looks.
    # Class D organisation (ADR 0042, settings registry).
    shortfall_horizon_days: int = Field(default=14, ge=1, le=60)
    # ADR 0044: changes by one planner per hour above which a system event warns (never
    # blocks). Class C platform (ADR 0042, settings registry).
    planner_changes_alert_per_hour: int = Field(default=50, ge=5, le=1000)

    # Links in messages (manage link, calendar UID) are built from this base URL.
    public_base_url: str = "http://127.0.0.1:8000"
    # Where Webex and WxCC deliver webhooks, if not PUBLIC_BASE_URL: an https tunnel or staging
    # host, while the OIDC redirect and the links stay on PUBLIC_BASE_URL (runbook, Webhooks).
    webhook_base_url: str | None = None

    # ADR 0011. Required in production; in development a shared key is generated in the data
    # volume (see app.keys).
    management_token_key: SecretStr | None = None
    dev_token_key_path: Path = Path("data/dev-token.key")

    # Outgoing mail: the fallback for organisations without their own SMTP server in the admin
    # portal (ADR 0022). Without SMTP_HOST the worker sends no such mail (jobs stay pending).
    # SMTP_TLS (or port 465) means implicit TLS; SMTP_STARTTLS upgrades a plain connection.
    # A login needs one of the two. SMTP_USER / SMTP_USER_PASSWORD are accepted as aliases.
    smtp_host: str | None = None
    smtp_port: int = 25
    smtp_starttls: bool = False
    smtp_tls: bool = False
    smtp_username: str | None = Field(
        default=None, validation_alias=AliasChoices("smtp_username", "smtp_user")
    )
    smtp_password: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("smtp_password", "smtp_user_password")
    )
    mail_from: str = "OpenPlanner <noreply@openplanner.invalid>"

    # Admin portal (ADR 0013). Without OIDC_DISCOVERY_URL the admin portal is disabled.
    session_secret: SecretStr | None = None
    dev_session_key_path: Path = Path("data/dev-session.key")
    admin_session_hours: int = 8
    # A change needs a login of at most this age; older sessions are sent to the IdP first,
    # which refreshes the roles (invisible with an active SSO session).
    admin_reauth_minutes: int = 60
    # ADR 0046, staff directory. An admin's "last seen" is written at most once per this many
    # seconds (class C, platform; ADR 0042 registry).
    staff_last_seen_interval_seconds: int = Field(default=300, ge=60, le=3600)
    # Without a login for this many days, a person is marked dormant in the staff list
    # (class D, organisation; one value for every organisation until the portal stores it).
    staff_dormant_after_days: int = Field(default=90, ge=14, le=365)
    admin_timezone: str = "Europe/Amsterdam"
    oidc_discovery_url: str | None = None
    oidc_client_id: str = "openplanner-admin"
    oidc_client_secret: SecretStr | None = None
    oidc_scopes: str = "openid profile email"
    oidc_role_claim: str = "roles"
    oidc_organisation_claim: str = "organisations"
    oidc_group_claim: str = "groups"
    oidc_role_superuser: str = "openplanner-superuser"
    oidc_role_org_admin: str = "openplanner-org-admin"
    oidc_role_group_admin: str = "openplanner-group-admin"
    oidc_role_auditor: str = "openplanner-auditor"

    # Integration secrets at rest (ADR 0008): AES-256-GCM under a key derived from this value.
    settings_encryption_key: SecretStr | None = None
    dev_settings_key_path: Path = Path("data/dev-settings.key")

    # Webex Service App (ADR 0017/0018): one app for meetings and WxCC work items. The refresh
    # token bootstraps the token store; later tokens are rotated and stored encrypted.
    webex_client_id: str | None = None
    webex_client_secret: SecretStr | None = None
    webex_refresh_token: SecretStr | None = None
    webex_api_base_url: str = "https://webexapis.com/v1"
    webex_webhook_secret: SecretStr | None = None
    # WxCC API host of the organisation's region, e.g. https://api.wxcc-eu1.cisco.com.
    wxcc_api_base_url: str | None = None
    wxcc_webhook_secret: SecretStr | None = None

    # Rate limits on public endpoints (CLAUDE.md security baseline).
    rate_limit_enabled: bool = True

    # Simulator for platform events on admin booking pages; off unless switched on, refused in
    # production (docs/runbook.md, section 10).
    simulator_enabled: bool = False
    rate_limit_bookings: str = "10/hour"
    rate_limit_reads: str = "60/minute"

    # ADR 0035: how many host websites one organisation may allow for the widget. Class C.
    embedding_max_origins: int = Field(default=20, ge=1, le=100)

    # Machine API for contact-center bots (ADR 0023). Off: the routes do not exist. On: the
    # superuser still switches it on in the admin portal and registers each client there.
    agent_api_enabled: bool = False
    rate_limit_agent_tokens: str = "10/minute"
    rate_limit_agent_calls: str = "120/minute"

    # Updates from the admin portal (ADR 0029). APP_VERSION is baked into release images by
    # main.yml; UPDATE_REPOSITORY (owner/name) switches the hourly release check on. The token
    # is only needed for a private repository and needs read access to its contents only.
    app_version: str = ""
    update_repository: str | None = None
    update_check_token: SecretStr | None = None

    # Maintenance mode (ADR 0038). MAINTENANCE_MODE (class B, deployment: needed while the
    # database is unavailable or being restored) switches it on from the environment; the
    # portal shows that and cannot switch it off.
    maintenance_mode: bool = False
    # Class C (platform, ADR 0042): how long the web process trusts its last read of the
    # maintenance switch; ADR 0038 sets 5 seconds as the upper bound.
    maintenance_cache_seconds: float = Field(default=5.0, ge=0, le=5)
    # Class C: the Retry-After of citizen pages and machine-API writes refused during
    # maintenance (seconds); maintenance has no automatic end, so this is a polite guess.
    maintenance_retry_after_seconds: int = Field(default=300, ge=30, le=3600)

    @model_validator(mode="after")
    def _storage_limits_are_ordered(self) -> "Settings":
        """Refuse a critical free-space limit that is not below the low one."""
        if self.storage_critical_free_percent >= self.storage_low_free_percent:
            raise ValueError("STORAGE_CRITICAL_FREE_PERCENT must be below STORAGE_LOW_FREE_PERCENT")
        return self

    @model_validator(mode="after")
    def _smtp_login_is_encrypted(self) -> "Settings":
        """Refuse an SMTP login that would send the password in clear text."""
        if (
            self.smtp_host
            and self.smtp_username
            and not (self.smtp_starttls or self.smtp_implicit_tls)
        ):
            raise ValueError("SMTP_USERNAME needs SMTP_STARTTLS or SMTP_TLS")
        return self

    @model_validator(mode="after")
    def _production_requirements(self) -> "Settings":
        """In production, refuse to start with development defaults."""
        if self.environment == "prod":
            if self.simulator_enabled:
                raise ValueError("SIMULATOR_ENABLED is not allowed in production")
            if self.management_token_key is None:
                raise ValueError("MANAGEMENT_TOKEN_KEY is required in production")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("PUBLIC_BASE_URL must use https in production")
            if self.session_secret is None:
                raise ValueError("SESSION_SECRET is required in production")
            if self.webhook_base_url is not None and not self.webhook_base_url.startswith(
                "https://"
            ):
                raise ValueError("WEBHOOK_BASE_URL must use https in production")
            if self.webex_client_id is not None and (
                self.webex_client_secret is None
                or self.webex_refresh_token is None
                or self.settings_encryption_key is None
            ):
                raise ValueError(
                    "Webex needs WEBEX_CLIENT_SECRET, WEBEX_REFRESH_TOKEN and "
                    "SETTINGS_ENCRYPTION_KEY in production"
                )
            if self.oidc_discovery_url is not None and (
                not self.oidc_discovery_url.startswith("https://")
                or self.oidc_client_secret is None
            ):
                raise ValueError("OIDC needs an https discovery URL and a client secret")
        return self

    @property
    def event_retention(self) -> EventRetention:
        """How long each category of audit event is kept (ADR 0040, part 4)."""
        return EventRetention(
            audit=timedelta(days=self.audit_retention_days),
            system=timedelta(days=self.system_event_retention_days),
            security=timedelta(days=self.security_event_retention_days),
            diagnostic=timedelta(days=self.diagnostic_event_retention_days),
        )

    @property
    def event_envelope(self) -> Envelope:
        """Release and installation name for every log line and event record (ADR 0040)."""
        return Envelope(self.app_version, self.installation_name)

    @property
    def smtp_implicit_tls(self) -> bool:
        """Whether the mail fallback opens a TLS connection: ``SMTP_TLS`` or port 465."""
        return self.smtp_tls or self.smtp_port == SMTPS_PORT

    @property
    def webhook_base(self) -> str:
        """Base URL the platforms call back on: ``WEBHOOK_BASE_URL`` or ``PUBLIC_BASE_URL``."""
        return (self.webhook_base_url or self.public_base_url).rstrip("/")

    @property
    def video_configured(self) -> bool:
        """Whether the Webex Service App is configured (meetings can be created)."""
        return bool(self.webex_client_id and self.webex_client_secret and self.webex_refresh_token)

    @property
    def dispatch_configured(self) -> bool:
        """Whether WxCC work items can be created (Service App plus the WxCC API host)."""
        return self.video_configured and bool(self.wxcc_api_base_url)

    @field_validator("update_repository")
    @classmethod
    def _repository_is_owner_and_name(cls, value: str | None) -> str | None:
        """Accept only ``owner/name``: it becomes part of an API URL."""
        if value is not None and (
            not _REPOSITORY.fullmatch(value) or {".", ".."} & set(value.split("/"))
        ):
            raise ValueError("UPDATE_REPOSITORY must be owner/name")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        """Accept ``debug`` as well as ``DEBUG``."""
        return value.upper() if isinstance(value, str) else value
