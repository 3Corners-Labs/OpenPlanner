import pytest

from app.config import Settings


def test_defaults_use_sqlite_in_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(environment="dev")

    assert settings.database_url == "sqlite+aiosqlite:///data/booking.db"


def test_reads_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db/booking")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("ENVIRONMENT", "dev")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://db/booking"
    assert settings.log_level == "DEBUG"


def test_rejects_unknown_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    monkeypatch.setenv("ENVIRONMENT", "dev")

    with pytest.raises(ValueError, match="log_level"):
        Settings()


def test_production_is_the_default_and_refuses_development_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("ENVIRONMENT", "MANAGEMENT_TOKEN_KEY", "PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="MANAGEMENT_TOKEN_KEY"):
        Settings()
    with pytest.raises(ValueError, match="https"):
        Settings(management_token_key="k" * 32)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="SESSION_SECRET"):
        Settings(
            management_token_key="k" * 32,  # type: ignore[arg-type]
            public_base_url="https://afspraak.example.nl",
        )
    production = {
        "management_token_key": "k" * 32,
        "public_base_url": "https://afspraak.example.nl",
        "session_secret": "s" * 32,
    }
    with pytest.raises(ValueError, match="OIDC"):
        Settings(**production, oidc_discovery_url="http://idp.local/.well-known")  # type: ignore[arg-type]

    settings = Settings(**production)  # type: ignore[arg-type]
    assert settings.environment == "prod"


def test_production_webex_needs_secrets_and_the_encryption_key() -> None:
    production = {
        "environment": "prod",
        "management_token_key": "m" * 40,
        "session_secret": "s" * 40,
        "public_base_url": "https://afspraak.example.nl",
    }
    with pytest.raises(ValueError, match="WEBEX_CLIENT_SECRET"):
        Settings(**production, webex_client_id="client")  # type: ignore[arg-type]
    configured = Settings(
        **production,  # type: ignore[arg-type]
        webex_client_id="client",
        webex_client_secret="secret",
        webex_refresh_token="refresh",
        settings_encryption_key="e" * 40,
    )
    assert configured.video_configured
    assert not configured.dispatch_configured
