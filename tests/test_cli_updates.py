"""Operator commands of the host updater (ADR 0029, ADR 0048): claim, steps, report, backup
and restore."""

from pathlib import Path

import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.cli import _backup, _restore, _update_claim, _update_progress, _update_report, main
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.update_service import UpdateService
from app.domain.updates import Release, ReleaseCheck, UpdateStatus, UpdateStep, Version
from tests.fakes.clock import FixedClock
from tests.support.database import migrated_database
from tests.support.domain import DEMO_SUPERUSER, NOW

RELEASE = Release(Version(1, 4, 0), "https://github.com/acme/openplanner/releases/tag/v1.4.0")


@pytest.fixture
def configured(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    url = migrated_database(tmp_path, monkeypatch, "updates")
    return settings.model_copy(update={"database_url": url, "app_version": "1.3.0"})


async def requested(settings: Settings) -> int:
    """Store a release check and a superuser's update request; return its id."""
    engine = create_engine(settings.database_url)
    try:
        uow, clock = SqlUnitOfWork(engine), FixedClock(NOW)
        async with uow.locked() as tx:
            await tx.save_release_check(ReleaseCheck(NOW, RELEASE))
        service = UpdateService(
            uow, clock, AdminService(uow, clock), current=Version(1, 3, 0), releases=None
        )
        created = await service.request_update(DEMO_SUPERUSER)
    finally:
        await engine.dispose()
    return created.id or 0


async def test_claim_prints_the_request_once(configured: Settings) -> None:
    request_id = await requested(configured)
    assert await _update_claim(configured) == f"{request_id} update 1.4.0 1.3.0\n"
    assert await _update_claim(configured) == ""


async def test_report_from_the_new_version(configured: Settings) -> None:
    request_id = await requested(configured)
    await _update_claim(configured)
    new = configured.model_copy(update={"app_version": "1.4.0"})
    assert await _update_report(new, request_id, "succeeded", "") == ("succeeded\n", 0)


async def test_report_refuses_nonsense(configured: Settings) -> None:
    request_id = await requested(configured)
    await _update_claim(configured)
    assert (await _update_report(configured, request_id, "running", ""))[1] == 2
    assert (await _update_report(configured, request_id, "failed", "Kapot!"))[1] == 1
    assert (await _update_report(configured, 999, "failed", "updater_error"))[1] == 1
    report = await _update_report(configured, request_id, "rolled_back", "health_check_failed")
    assert report == ("rolled_back\n", 0)
    assert (await _update_report(configured, request_id, "failed", ""))[1] == 1


async def test_progress_records_the_step(configured: Settings) -> None:
    request_id = await requested(configured)
    assert (await _update_progress(configured, request_id, "pulling"))[1] == 1  # not claimed
    await _update_claim(configured)
    assert await _update_progress(configured, request_id, "backing_up") == ("backing_up\n", 0)
    assert (await _update_progress(configured, request_id, "pulling"))[1] == 1  # going back
    assert (await _update_progress(configured, request_id, "waiting"))[1] == 2
    assert (await _update_progress(configured, request_id, "rm -rf"))[1] == 2


def test_backup_restore_and_prune(configured: Settings) -> None:
    assert _backup(configured, "request-1", prune=True).startswith("backup written: request-1")
    assert "restored request-1" in _restore(configured, "request-1")
    assert _backup(configured, None, prune=True) == "old backups deleted: 0\n"


async def test_restored_database_has_the_state_of_the_backup(configured: Settings) -> None:
    _backup(configured, "before", prune=False)
    await requested(configured)
    _restore(configured, "before")
    engine = create_engine(configured.database_url)
    try:
        async with SqlUnitOfWork(engine).read() as tx:
            assert await tx.get_update_requests() == []
    finally:
        await engine.dispose()


def test_main_rejects_an_unknown_step(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["update-progress", "1", "waiting"])
    assert UpdateStep.PULLING.value in capsys.readouterr().err


def test_main_rejects_an_unknown_status(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["update-report", "1", "done"])
    assert UpdateStatus.SUCCEEDED.value in capsys.readouterr().err
