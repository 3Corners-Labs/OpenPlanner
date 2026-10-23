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

    settings = Settings(
        management_token_key="k" * 32,  # type: ignore[arg-type]
        public_base_url="https://afspraak.example.nl",
    )
    assert settings.environment == "prod"
