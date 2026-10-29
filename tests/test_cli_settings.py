"""``python -m app.cli settings`` and ``settings-doc`` (ADR 0042 §10)."""

import sqlite3
from pathlib import Path

import pytest

from app.cli import main
from tests.support.database import migrated_database


@pytest.fixture(autouse=True)
def _dev(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ENVIRONMENT", "dev")
    # A directory is no SQLite file: the portal values cannot be read here.
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}")


def test_settings_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["settings", "list"]) == 0
    out = capsys.readouterr().out

    assert "\nVARIABLE" in out
    assert "RATE_LIMIT_BOOKINGS" in out


def test_without_a_database_the_list_says_so_and_still_shows_everything(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["settings", "list"]) == 0
    out = capsys.readouterr().out

    assert out.startswith("Portal values not read (")
    assert "ADMIN_SESSION_HOURS" in out


def test_a_stored_portal_value_is_listed_with_its_source(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = migrated_database(tmp_path, monkeypatch, "cli")
    with sqlite3.connect(url.removeprefix("sqlite+aiosqlite:///")) as db:
        db.execute(
            "INSERT INTO platform_settings (key, value, updated_at) "
            "VALUES ('admin_session_hours', '4', '2026-10-29 12:00:00')"
        )
        db.execute("INSERT INTO settings_version (id, version) VALUES (1, 1)")

    assert main(["settings", "show", "ADMIN_SESSION_HOURS"]) == 0
    out = capsys.readouterr().out

    assert "value:         4" in out
    assert "source:        portal" in out


def test_settings_show_accepts_any_case(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["settings", "show", "rate_limit_reads"]) == 0

    assert "variable:      RATE_LIMIT_READS" in capsys.readouterr().out


def test_settings_show_unknown_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["settings", "show", "NO_SUCH_SETTING"]) == 2

    assert "Unknown setting" in capsys.readouterr().out


def test_settings_doc(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["settings-doc"]) == 0

    assert capsys.readouterr().out.startswith("# Settings")


def test_init_writes_the_keys_and_never_prints_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Before the environment is complete: production without any key must still work.
    monkeypatch.setenv("ENVIRONMENT", "prod")
    target = tmp_path / "secrets" / "secrets.env"

    assert main(["init", "--out", str(target)]) == 0
    out = capsys.readouterr().out

    keys = [line.split("=", 1)[1] for line in target.read_text().splitlines() if "=" in line]
    assert len(keys) == 3
    assert not any(key in out for key in keys)
    assert "PUBLIC_BASE_URL" in out


def test_init_refuses_an_existing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "secrets.env"
    target.write_text("# keep\n")

    assert main(["init", "--out", str(target)]) == 1
    assert "nothing written" in capsys.readouterr().out
    assert target.read_text() == "# keep\n"
