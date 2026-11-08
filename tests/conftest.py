"""Shared fixtures: isolated settings per test, an in-process HTTP client and a mail server."""

import smtplib
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from pydantic import SecretStr

from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.activity_alert import ALERTS
from app.domain.audit import AuditEvent, Outcome
from app.domain.event_context import set_service
from app.domain.event_schema import detail_problem
from app.domain.log_events import lookup
from app.domain.ports import Clock
from app.main import create_app
from tests.fakes.smtp import FakeSmtpServer

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app") + "/"
READ_BACK = APP + "adapters/db/"
"""Where events are built from stored rows; the action was checked when it was written."""

COUNTED = frozenset(alert.permission.value for alert in ALERTS)
"""Actions an hourly alert counts (``app.domain.activity_alert``)."""

ALERTED = ("domain/admin.py", "_change_event")
"""Where a counted success is built: for ``AdminService.record_change``, which runs its alert."""


def built_outside_its_alert(event: AuditEvent, where: str, function: str) -> bool:
    """Whether ``event`` is a success an hourly alert counts, built where its check never runs."""
    counted = event.outcome is Outcome.SUCCESS and event.action in COUNTED
    return counted and (where, function) != ALERTED


@pytest.fixture(autouse=True, scope="session")
def _audited_actions_are_registered() -> Iterator[None]:
    """Every action the application audits while the tests run is registered (ADR 0103).

    Audit, system and security events build their action in many ways (a permission, a
    constant, ``job.<type>``), so this watches the events the code in ``app/`` creates instead
    of reading the source; ``tests/test_log_events_scan.py`` covers the log calls. The same
    watch checks that every detail follows the convention the line's ``labels.*`` are read
    from (ADR 0103 point 6, ``app.domain.event_schema.detail_problem``), and that a success an
    hourly alert counts comes from ``AdminService.record_change`` (ADR 0044).
    """
    seen: dict[str, str] = {}
    malformed: dict[str, str] = {}
    unchecked: dict[str, str] = {}
    original = AuditEvent.__post_init__

    def watched(event: AuditEvent) -> None:
        original(event)
        caller = sys._getframe(2).f_code  # 1: the dataclass __init__, 2: who built the event
        if caller.co_filename.startswith(APP) and not caller.co_filename.startswith(READ_BACK):
            where = caller.co_filename.removeprefix(APP)
            seen.setdefault(event.action, where)
            problem = detail_problem(event.detail)
            if problem is not None:
                malformed.setdefault(f"{where} ({event.action})", problem)
            if built_outside_its_alert(event, where, caller.co_name):
                unchecked.setdefault(f"{where} ({caller.co_name})", event.action)

    AuditEvent.__post_init__ = watched  # type: ignore[method-assign]
    yield
    AuditEvent.__post_init__ = original  # type: ignore[method-assign]
    unknown = {action: where for action, where in seen.items() if lookup(action) is None}
    assert unknown == {}, "audited actions missing from app/domain/log_events.py"
    assert malformed == {}, "audit details that break the convention of app/domain/event_schema"
    assert unchecked == {}, "counted successes built outside AdminService.record_change"


@pytest.fixture(autouse=True)
def _no_service_name() -> Iterator[None]:
    """Forget the process-wide service name an app's lifespan sets (ADR 0040)."""
    yield
    set_service("")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        worker_heartbeat_path=tmp_path / "worker.heartbeat",
        worker_poll_seconds=0.01,
        management_token_key=SecretStr("t" * 32),
        dev_token_key_path=tmp_path / "dev-token.key",
        dev_settings_key_path=tmp_path / "dev-settings.key",
        session_secret=SecretStr("x" * 32),
        rate_limit_enabled=False,
        # Storage grades (ADR 0040) must not depend on the free space of the test machine.
        storage_low_free_percent=0.002,
        storage_critical_free_percent=0.001,
    )


@pytest.fixture
def unreachable_db_settings(settings: Settings, tmp_path: Path) -> Settings:
    """Settings whose database cannot be opened: a directory is not a SQLite file."""
    return settings.model_copy(update={"database_url": f"sqlite+aiosqlite:///{tmp_path}"})


@asynccontextmanager
async def app_client(
    settings: Settings, clock: Clock | None = None, integrations: Integrations | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's lifespan and yield a client that talks to it in-process."""
    app = create_app(settings, clock, integrations=integrations)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield client


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(settings) as client:
        yield client


@pytest.fixture
def smtp_server(monkeypatch: pytest.MonkeyPatch) -> FakeSmtpServer:
    """Connect ``smtplib.SMTP`` and ``smtplib.SMTP_SSL`` to a fake server for one test."""
    server = FakeSmtpServer()
    monkeypatch.setattr(smtplib, "SMTP", server.connect)
    monkeypatch.setattr(smtplib, "SMTP_SSL", server.connect_tls)
    return server


@pytest.fixture
def alembic_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Return the Alembic configuration for a fresh SQLite file in ``tmp_path`` (migrate.db)."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'migrate.db'}")
    monkeypatch.setenv("ENVIRONMENT", "test")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config
