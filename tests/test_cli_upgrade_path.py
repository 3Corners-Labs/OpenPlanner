"""``upgrade-path-check``: a database older than the image's stop is refused (ADR 0116 point 6)."""

from pathlib import Path

import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.cli import _upgrade_path_check, main
from app.config import Settings
from app.domain.updates import RunningVersion
from tests.support.database import migrated_database
from tests.support.domain import NOW


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("ENVIRONMENT", "dev")
    url = migrated_database(tmp_path, monkeypatch, "path")
    monkeypatch.setenv("DATABASE_URL", url)
    return Settings(database_url=url, app_version="1.2.0", min_upgrade_from="1.0.0")


async def _ran(settings: Settings, version: str) -> None:
    engine = create_engine(settings.database_url)
    try:
        async with SqlUnitOfWork(engine).locked() as tx:
            await tx.add_running_version(RunningVersion(version, "", NOW, None))
    finally:
        await engine.dispose()


async def test_a_database_older_than_the_stop_is_refused_naming_it(settings: Settings) -> None:
    await _ran(settings, "0.9.4")

    report, ok = await _upgrade_path_check(settings, None, None)

    assert not ok
    assert report == "upgrade_path_required: upgrade to 1.0.0 first (this database ran 0.9.4)\n"


async def test_a_new_database_or_one_past_the_stop_goes_ahead(settings: Settings) -> None:
    assert await _upgrade_path_check(settings, None, None) == ("upgrade path ok\n", True)
    await _ran(settings, "1.0.3")
    assert (await _upgrade_path_check(settings, None, None))[1]


async def test_the_updater_asks_for_the_target_image(settings: Settings) -> None:
    await _ran(settings, "1.0.3")

    report, ok = await _upgrade_path_check(settings, "2.1.0", "2.0.0")

    assert not ok
    assert "upgrade to 2.0.0 first" in report
    assert (await _upgrade_path_check(settings, "1.9.0", "2.0.0"))[1]  # before the stop


def test_the_command_runs_from_the_command_line(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_VERSION", "1.2.0")

    assert main(["upgrade-path-check"]) == 0
    assert main(["upgrade-path-check", "--image-version", "3.0.0", "--stop", "3.0.0"]) == 0
    assert "upgrade path ok" in capsys.readouterr().out
