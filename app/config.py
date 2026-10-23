"""Application settings, read from the environment only."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Typed configuration. Each field maps to the upper-case environment variable.

    Secrets never get defaults here; they come from the environment or the secret store.
    ``.env.example`` lists every variable with a placeholder.

    Secure by default: ``ENVIRONMENT`` defaults to ``prod``, which requires
    ``MANAGEMENT_TOKEN_KEY`` and an ``https`` ``PUBLIC_BASE_URL``. Development sets
    ``ENVIRONMENT=dev`` explicitly (``.env.example``).
    """

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    environment: Literal["dev", "test", "prod"] = "prod"
    database_url: str = "sqlite+aiosqlite:///data/booking.db"
    log_level: LogLevel = "INFO"

    # Worker loop: poll interval and the heartbeat file its container healthcheck reads.
    worker_poll_seconds: float = 30.0
    worker_heartbeat_path: Path = Path("data/worker.heartbeat")
    worker_heartbeat_max_age_seconds: float = 120.0

    # Links in messages (manage link, calendar UID) are built from this base URL.
    public_base_url: str = "http://127.0.0.1:8000"

    # ADR 0011. Required in production; in development a shared key is generated in the data
    # volume (see app.keys).
    management_token_key: SecretStr | None = None
    dev_token_key_path: Path = Path("data/dev-token.key")

    # Outgoing mail. Without SMTP_HOST the worker sends no mail (jobs stay pending).
    # Per-organisation SMTP settings in the admin UI follow in WP7 (ADR 0008).
    smtp_host: str | None = None
    smtp_port: int = 25
    smtp_starttls: bool = False
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
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

    # Rate limits on public endpoints (CLAUDE.md security baseline).
    rate_limit_enabled: bool = True
    rate_limit_bookings: str = "10/hour"
    rate_limit_reads: str = "60/minute"

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
            if self.oidc_discovery_url is not None and (
                not self.oidc_discovery_url.startswith("https://")
                or self.oidc_client_secret is None
            ):
                raise ValueError("OIDC needs an https discovery URL and a client secret")
        return self

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        """Accept ``debug`` as well as ``DEBUG``."""
        return value.upper() if isinstance(value, str) else value
