"""The ``sessions end-all`` command (ADR 0098 point 9c): what the host and the updater run."""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.adapters.db.engine import create_engine
from app.adapters.db.models import AuditEventRow
from app.adapters.db.uow import SqlUnitOfWork
from app.cli import _sessions_end_all, main
from app.config import Settings
from app.domain.admin_sessions import AdminSession, EndReason
from app.domain.entitlements import LoginClaims
from tests.support.database import migrated_database
from tests.support.domain import NOW


@pytest.fixture
def configured(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    url = migrated_database(tmp_path, monkeypatch, "cli-sessions")
    return settings.model_copy(update={"database_url": url})


async def _rows(settings: Settings) -> tuple[list[str | None], list[Any]]:
    engine = create_engine(settings.database_url)
    try:
        uow = SqlUnitOfWork(engine, None)
        async with uow.read() as tx:
            reasons = [
                (await tx.get_admin_session(f"{index}" * 64)).end_reason  # type: ignore[union-attr]
                for index in range(3)
            ]
        async with engine.connect() as connection:
            audit = list((await connection.execute(select(AuditEventRow))).all())
    finally:
        await engine.dispose()
    return [r.value if r else None for r in reasons], audit


async def _seed(settings: Settings) -> None:
    engine = create_engine(settings.database_url)
    try:
        uow = SqlUnitOfWork(engine, None)
        claims = LoginClaims("sub-1", "Sanne")
        async with uow.locked() as tx:
            for index in range(3):
                await tx.add_admin_session(
                    AdminSession(f"{index}" * 64, f"sub-{index}", "platform", claims, NOW, NOW)
                )
            await tx.end_admin_session("2" * 64, EndReason.LOGOUT, NOW)
    finally:
        await engine.dispose()


async def test_end_all_ends_every_live_session_with_one_audit_event(configured: Settings) -> None:
    await _seed(configured)

    line = await _sessions_end_all(configured, EndReason.RESTORE)

    reasons, audit = await _rows(configured)
    assert line == "ended 2 sessions (restore)\n"
    assert reasons == ["restore", "restore", "logout"]
    assert len(audit) == 1
    event = audit[0]
    assert (event.actor, event.action, event.reason, event.detail) == (
        "system:cli",
        "admin.sessions_ended_all",
        "restore",
        "sessions=2",
    )


async def test_end_all_without_sessions_still_leaves_one_audit_event(configured: Settings) -> None:
    await _sessions_end_all(configured, EndReason.CLI)

    engine = create_engine(configured.database_url)
    try:
        async with engine.connect() as connection:
            audit = list((await connection.execute(select(AuditEventRow))).all())
    finally:
        await engine.dispose()
    assert [(e.reason, e.detail) for e in audit] == [("cli", "sessions=0")]


def test_the_parser_wants_a_reason_from_the_closed_list(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for argv in (
        ["sessions", "end-all"],
        ["sessions", "end-all", "--reason", "because"],
        ["sessions", "end-all", "--reason", "idle"],
    ):
        with pytest.raises(SystemExit):
            main(argv)
    assert "--reason" in capsys.readouterr().err


def test_main_runs_end_all(
    configured: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", configured.database_url)

    assert main(["sessions", "end-all", "--reason", "restore"]) == 0
    assert capsys.readouterr().out == "ended 0 sessions (restore)\n"
