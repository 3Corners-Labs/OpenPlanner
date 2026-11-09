"""``config export --format json``, ``config plan`` and ``config apply`` (ADR 0099 point 13)."""

import asyncio
import json
import stat
from pathlib import Path

import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.cli import main
from tests.support.database import migrated_database

PASSPHRASE = "a passphrase of sixteen chars"  # noqa: S105 - a test passphrase


async def _seed(url: str) -> None:
    engine = create_engine(url)
    try:
        await seed_demo(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A migrated, seeded database as ``DATABASE_URL``, with a development settings key."""
    url = migrated_database(tmp_path, monkeypatch, "cli-document")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("DEV_SETTINGS_KEY_PATH", str(tmp_path / "dev-settings.key"))
    monkeypatch.setenv(
        "OIDC_DISCOVERY_URL", "http://keycloak:8080/realms/x/.well-known/openid-configuration"
    )
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "dev-only-secret-value")
    asyncio.run(_seed(url))
    return url


def test_export_plan_and_apply_a_document(
    seeded: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = tmp_path / "config.json"

    assert main(["config", "export", str(document), "--format", "json", "--no-secrets"]) == 0
    assert stat.S_IMODE(document.stat().st_mode) == 0o600
    data = json.loads(document.read_text())
    assert data["format"] == "openplanner.config/v1"
    capsys.readouterr()

    assert main(["config", "plan", str(document)]) == 0
    plan = capsys.readouterr().out
    assert "0 change(s), 0 refusal(s)" in plan
    assert "plan hash " in plan

    data["organisations"][0]["name"] = "Hernoemd"
    document.write_text(json.dumps(data))
    assert main(["config", "plan", str(document)]) == 0
    assert "update organisation" in capsys.readouterr().out
    assert main(["config", "apply", str(document)]) == 0
    assert "applied; settings version" in capsys.readouterr().out
    assert main(["config", "plan", str(document)]) == 0
    assert "0 change(s)" in capsys.readouterr().out


def test_a_refused_document_prints_its_refusals_and_exits_with_two(
    seeded: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = tmp_path / "bad.json"
    document.write_text(
        json.dumps(
            {
                "format": "openplanner.config/v1",
                "organisations": [{"slug": "demo", "groups": [{"slug": "nieuw"}]}],
            }
        )
    )

    assert main(["config", "plan", str(document)]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert main(["config", "apply", str(document)]) == 2
    assert "nothing applied" in capsys.readouterr().out


def test_a_document_that_is_not_json_is_refused(
    seeded: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = tmp_path / "broken.json"
    document.write_text("{not json")

    assert main(["config", "plan", str(document)]) == 2
    assert "configuration_package_invalid" in capsys.readouterr().out


def test_the_schema_command_prints_the_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config-schema"]) == 0
    assert json.loads(capsys.readouterr().out)["title"].startswith("OpenPlanner configuration")


def test_the_sqlite_export_still_needs_a_passphrase(
    seeded: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["config", "export", str(tmp_path / "x.sqlite")]) == 2
    assert "configuration_passphrase_short" in capsys.readouterr().out


def test_a_failed_export_keeps_the_old_file_and_the_file_is_private(
    seeded: str, tmp_path: Path
) -> None:
    document = tmp_path / "config.json"
    document.write_text("old")

    assert main(["config", "export", str(document), "--format", "json", "--no-secrets"]) == 0
    assert document.read_text() != "old"
    assert stat.S_IMODE(document.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".config.json.*"))


def test_a_document_above_the_size_limit_is_refused_before_it_is_read(
    seeded: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CONFIGURATION_PACKAGE_MAX_MB", "1")
    document = tmp_path / "big.json"
    document.write_bytes(b" " * (1024 * 1024 + 1))

    assert main(["config", "plan", str(document)]) == 2
    assert "configuration_package_too_large" in capsys.readouterr().out


def test_a_plain_export_is_refused_unless_the_host_allows_it(
    seeded: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    document = tmp_path / "plain.json"

    assert main(["config", "export", str(document), "--plain-secrets"]) == 2
    assert "configuration_plaintext_disabled" in capsys.readouterr().out
    assert not document.exists()

    monkeypatch.setenv("CONFIGURATION_PLAINTEXT_EXPORT_ALLOWED", "true")
    assert main(["config", "export", str(document), "--plain-secrets"]) == 0
    assert stat.S_IMODE(document.stat().st_mode) == 0o600
    assert b'"sealed"' not in document.read_bytes()
    assert "seal" not in json.loads(document.read_text())
    capsys.readouterr()
    assert main(["config", "plan", str(document)]) == 0
    assert "0 change(s), 0 refusal(s)" in capsys.readouterr().out
