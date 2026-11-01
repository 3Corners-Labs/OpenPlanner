"""``python -m app.cli config export|show|import|env`` (ADR 0089 point 6)."""

import asyncio
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.cli import main
from tests.support.database import migrated_database

PASSPHRASE = "a passphrase of sixteen chars"  # noqa: S105 - a test passphrase


@pytest.fixture
def passphrase_file(tmp_path: Path) -> Path:
    path = tmp_path / "passphrase"
    path.write_text(PASSPHRASE + "\n")
    return path


async def _seed(url: str) -> None:
    engine = create_engine(url)
    try:
        await seed_demo(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A migrated, seeded database as ``DATABASE_URL``, with a development settings key."""
    url = migrated_database(tmp_path, monkeypatch, "cli-config")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("DEV_SETTINGS_KEY_PATH", str(tmp_path / "dev-settings.key"))
    monkeypatch.setenv(
        "OIDC_DISCOVERY_URL", "http://keycloak:8080/realms/x/.well-known/openid-configuration"
    )
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "dev-only-secret-value")
    asyncio.run(_seed(url))
    return url


def test_export_show_env_and_import_round_trip(
    seeded: str,
    passphrase_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = tmp_path / "config.sqlite"
    args = ["--passphrase-file", str(passphrase_file)]

    assert main(["config", "export", str(package), *args]) == 0
    assert stat.S_IMODE(package.stat().st_mode) == 0o600
    assert main(["config", "show", str(package), *args]) == 0
    out = capsys.readouterr().out
    assert "organisations       2" in out
    assert "OIDC_CLIENT_SECRET" in out  # the variable's name, never its value
    assert "dev-only-secret-value" not in out

    env_file = tmp_path / "config.env"
    assert main(["config", "env", str(package), *args, "--out", str(env_file)]) == 0
    assert "OIDC_CLIENT_SECRET=dev-only-secret-value\n" in env_file.read_text()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "dev-only-secret-value" not in capsys.readouterr().out
    # The installer reads the login through a one-off container with a read-only filesystem.
    assert main(["config", "env", str(package), *args, "--out", "-"]) == 0
    assert "OIDC_CLIENT_SECRET=dev-only-secret-value\n" in capsys.readouterr().out

    target = migrated_database(tmp_path, monkeypatch, "cli-target")
    assert main(["config", "import", str(package), *args]) == 0
    assert "configuration imported" in capsys.readouterr().out
    with closing(sqlite3.connect(target.removeprefix("sqlite+aiosqlite:///"))) as db:
        assert db.execute("SELECT COUNT(*) FROM organisations").fetchone()[0] == 2
        actors = [r[0] for r in db.execute("SELECT actor FROM audit_events")]
    assert actors == ["system:cli"]


def test_a_wrong_passphrase_is_refused_by_code(
    seeded: str,
    passphrase_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = tmp_path / "config.sqlite"
    assert main(["config", "export", str(package), "--passphrase-file", str(passphrase_file)]) == 0
    wrong = tmp_path / "wrong"
    wrong.write_text("another passphrase entirely\n")

    assert main(["config", "show", str(package), "--passphrase-file", str(wrong)]) == 2

    assert "Refused (configuration_passphrase_wrong)." in capsys.readouterr().out


def test_a_short_passphrase_writes_nothing(
    seeded: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    short = tmp_path / "short"
    short.write_text("short\n")
    package = tmp_path / "config.sqlite"

    assert main(["config", "export", str(package), "--passphrase-file", str(short)]) == 2

    assert not package.exists()
    assert "Refused (configuration_passphrase_short)." in capsys.readouterr().out
