"""Shared fixtures: isolated settings per test, in-process HTTP clients and a mail server.

``site`` is the admin site with the demo data on its own database ``admin_db``; the admin and
the web tests share it (``tests/api`` has a ``site`` of its own). Its harness is in
``tests/support/admin_site.py``.
"""

import smtplib
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from pydantic import SecretStr

from app.config import Settings
from app.domain.audit import AuditEvent
from app.domain.event_context import set_service
from app.domain.event_schema import detail_problem
from app.domain.log_events import lookup
from tests.fakes.smtp import FakeSmtpServer
from tests.support.admin_site import Site, running_site
from tests.support.app_client import app_client
from tests.support.audit_watch import checked_by_its_alert
from tests.support.database import migrated_database
from tests.support.git_repository import Git, repository

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app") + "/"
READ_BACK = APP + "adapters/db/"
"""Where events are built from stored rows; the action was checked when it was written."""


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
            if not checked_by_its_alert(event, sys._getframe(2)):
                unchecked.setdefault(f"{where} ({caller.co_qualname})", event.action)

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


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(settings) as client:
        yield client


@pytest.fixture
def admin_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "admin")


@pytest.fixture
async def site(settings: Settings, admin_db: str) -> AsyncIterator[Site]:
    async with running_site(settings, admin_db) as running:
        yield running


@pytest.fixture
def smtp_server(monkeypatch: pytest.MonkeyPatch) -> FakeSmtpServer:
    """Connect ``smtplib.SMTP`` and ``smtplib.SMTP_SSL`` to a fake server for one test."""
    server = FakeSmtpServer()
    monkeypatch.setattr(smtplib, "SMTP", server.connect)
    monkeypatch.setattr(smtplib, "SMTP_SSL", server.connect_tls)
    return server


@pytest.fixture
def git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Git:
    """A git repository on branch ``main`` in ``tmp_path``, which is the working directory."""
    return repository(tmp_path, monkeypatch)


@pytest.fixture
def alembic_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Return the Alembic configuration for a fresh SQLite file in ``tmp_path`` (migrate.db)."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'migrate.db'}")
    monkeypatch.setenv("ENVIRONMENT", "test")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config
