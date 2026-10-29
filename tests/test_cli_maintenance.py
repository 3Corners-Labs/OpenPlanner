"""The maintenance command of the CLI (ADR 0038): what the host updater and operators run."""

from pathlib import Path

import pytest

from app.cli import _maintenance, main
from app.config import Settings
from tests.support.database import migrated_database


@pytest.fixture
def configured(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    url = migrated_database(tmp_path, monkeypatch, "maintenance")
    return settings.model_copy(update={"database_url": url})


async def test_on_status_off(configured: Settings) -> None:
    assert await _maintenance(configured, "status", "other", "host-cli") == "off\n"

    line = await _maintenance(configured, "on", "update", "host-updater")
    assert line.startswith("on since=")
    assert line.endswith(" by=host-updater reason=update\n")
    assert await _maintenance(configured, "status", "other", "host-cli") == line

    assert await _maintenance(configured, "off", "other", "host-updater") == "off\n"


async def test_environment_override_is_reported_and_stays_on(configured: Settings) -> None:
    forced = configured.model_copy(update={"maintenance_mode": True})

    assert await _maintenance(forced, "off", "other", "host-cli") == "on environment\n"


def test_the_parser_refuses_free_text(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (
        ["maintenance", "on", "--reason", "because"],
        ["maintenance", "on", "--by", "someone"],
        ["maintenance", "pause"],
    ):
        with pytest.raises(SystemExit):
            main(argv)
    assert "invalid choice" in capsys.readouterr().err
