"""Application settings, read from the environment only."""

import re
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.i18n.dates import ClockStyle

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

AGENT_API_PREFIX = "/api/agent/v1"
"""Where the machine API for contact-center bots is mounted (ADR 0023)."""

SMTPS_PORT = 465
"""Mail submission over implicit TLS (RFC 8314); STARTTLS is never offered there."""

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


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
    rate_limit_bookings: str = "10/hour"
    rate_limit_reads: str = "60/minute"

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
