import pytest

from app.config import Settings


def test_defaults_use_sqlite_in_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings()

    assert settings.database_url == "sqlite+aiosqlite:///data/booking.db"


def test_reads_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db/booking")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://db/booking"
    assert settings.log_level == "DEBUG"


def test_rejects_unknown_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "chatty")

    with pytest.raises(ValueError, match="log_level"):
        Settings()
