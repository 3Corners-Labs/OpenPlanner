"""``python -m app.cli rekey`` and a key rotation without loss (ADR 0094 points 2 and 3)."""

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from app.adapters.db.credentials import CredentialStore
from app.adapters.db.engine import create_engine
from app.adapters.integrations import installation_box
from app.cli import main
from app.config import Settings
from tests.support.database import migrated_database

OLD = "o" * 40
NEW = "n" * 40
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
PASSPHRASE = "a passphrase of sixteen chars"  # noqa: S105 - a test passphrase


async def _put(url: str, value: dict[str, str]) -> None:
    engine = create_engine(url)
    try:
        box = installation_box(Settings())
        await CredentialStore(engine, box).put("webex_service_app", value, NOW)
    finally:
        await engine.dispose()


async def _get(url: str) -> dict[str, str] | None:
    engine = create_engine(url)
    try:
        return await CredentialStore(engine, installation_box(Settings())).get("webex_service_app")
    finally:
        await engine.dispose()


def _nonces(url: str) -> list[bytes]:
    with closing(sqlite3.connect(url.removeprefix("sqlite+aiosqlite:///"))) as db:
        return [r[0] for r in db.execute("SELECT nonce FROM integration_credentials")]


def _audit(url: str) -> list[tuple[str, str, str]]:
    with closing(sqlite3.connect(url.removeprefix("sqlite+aiosqlite:///"))) as db:
        return db.execute("SELECT actor, action, detail FROM audit_events ORDER BY id").fetchall()


@pytest.fixture
def installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A migrated database whose settings key is ``OLD``, holding one sealed secret."""
    url = migrated_database(tmp_path, monkeypatch, "rekey")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", OLD)
    asyncio.run(_put(url, {"token": "kept"}))
    return url


def test_rotation_loses_nothing_and_rekey_is_idempotent(
    installation: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _nonces(installation)
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", NEW)

    # A new key and nothing else: the old secret is unreadable (what the rotation avoids).
    with pytest.raises(InvalidTag):
        asyncio.run(_get(installation))

    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY_PREVIOUS", OLD)
    assert asyncio.run(_get(installation)) == {"token": "kept"}  # readable under the previous key

    assert main(["rekey"]) == 0
    first = capsys.readouterr().out
    assert "resealed 1 secrets" in first
    assert OLD not in first
    assert NEW not in first
    assert _nonces(installation) != before

    monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY_PREVIOUS")
    assert asyncio.run(_get(installation)) == {"token": "kept"}  # the previous key may go

    after = _nonces(installation)
    assert main(["rekey"]) == 0
    assert "resealed 0 secrets" in capsys.readouterr().out
    assert _nonces(installation) == after  # idempotent: nothing is touched again
    events = _audit(installation)
    assert [(actor, detail) for actor, _, detail in events] == [
        ("system:cli", "secrets.rekey resealed=1 current=0"),
        ("system:cli", "secrets.rekey resealed=0 current=1"),
    ]


def test_without_the_previous_key_rekey_changes_nothing(
    installation: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _nonces(installation)
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", NEW)

    assert main(["rekey"]) == 1

    assert "SETTINGS_ENCRYPTION_KEY_PREVIOUS" in capsys.readouterr().out
    assert _nonces(installation) == before
    assert _audit(installation) == []


def test_a_configuration_package_survives_a_rotation(
    installation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ADR 0089 and ADR 0094 share the re-sealing: export during and import after a rotation."""
    passphrase = tmp_path / "passphrase"
    passphrase.write_text(PASSPHRASE + "\n")
    package = tmp_path / "config.sqlite"
    args = ["--passphrase-file", str(passphrase)]
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", NEW)
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY_PREVIOUS", OLD)

    assert main(["config", "export", str(package), *args]) == 0  # opens the value under OLD
    capsys.readouterr()
    target = migrated_database(tmp_path, monkeypatch, "rekey-target")
    monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY_PREVIOUS")
    assert main(["config", "import", str(package), *args]) == 0

    assert asyncio.run(_get(target)) == {"token": "kept"}


def test_a_short_previous_key_is_refused_without_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY_PREVIOUS", "too-short-secret")

    with pytest.raises(
        ValueError, match="SETTINGS_ENCRYPTION_KEY_PREVIOUS must be at least 32"
    ) as e:
        Settings()

    assert "too-short-secret" not in str(e.value)
