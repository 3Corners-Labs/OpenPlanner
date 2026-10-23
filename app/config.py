"""Application settings, read from the environment only."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Typed configuration. Each field maps to the upper-case environment variable.

    Secrets never get defaults here; they come from the environment or the secret store.
    ``.env.example`` lists every variable with a placeholder.
    """

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    environment: Literal["dev", "test", "prod"] = "dev"
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

    # Rate limits on public endpoints (CLAUDE.md security baseline).
    rate_limit_enabled: bool = True
    rate_limit_bookings: str = "10/hour"
    rate_limit_reads: str = "60/minute"

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        """Accept ``debug`` as well as ``DEBUG``."""
        return value.upper() if isinstance(value, str) else value
