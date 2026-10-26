import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.jobs.healthcheck import heartbeat_is_fresh
from app.jobs.worker import fallback_mail, run_worker
from tests.fakes.clock import FixedClock

SMTP_VARIABLES = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_STARTTLS",
    "SMTP_TLS",
    "SMTP_USERNAME",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_USER_PASSWORD",
    "MAIL_FROM",
)


@pytest.fixture
def mail_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in SMTP_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.nl")
    return monkeypatch


@pytest.mark.parametrize(
    ("user_variable", "password_variable"),
    [("SMTP_USERNAME", "SMTP_PASSWORD"), ("SMTP_USER", "SMTP_USER_PASSWORD")],
)
def test_fallback_mail_logs_in_with_the_environment_credentials(
    mail_env: pytest.MonkeyPatch, user_variable: str, password_variable: str
) -> None:
    test_pw = "pw-from-env"  # noqa: S105 - test value
    mail_env.setenv("SMTP_PORT", "587")
    mail_env.setenv("SMTP_STARTTLS", "true")
    mail_env.setenv(user_variable, "planner@example.nl")
    mail_env.setenv(password_variable, test_pw)
    mail_env.setenv("MAIL_FROM", "Planner <noreply@example.nl>")

    config = fallback_mail(Settings(environment="dev"))

    assert config is not None
    assert (config.host, config.port, config.sender) == (
        "smtp.example.nl",
        587,
        "Planner <noreply@example.nl>",
    )
    assert (config.username, config.password) == ("planner@example.nl", test_pw)
    assert config.starttls
    assert not config.implicit_tls


@pytest.mark.parametrize(
    ("port", "tls"), [("465", None), ("2465", "true")], ids=["port-465", "smtp-tls"]
)
def test_fallback_mail_uses_implicit_tls_on_465_or_when_asked(
    mail_env: pytest.MonkeyPatch, port: str, tls: str | None
) -> None:
    mail_env.setenv("SMTP_PORT", port)
    mail_env.setenv("SMTP_STARTTLS", "true")  # meaningless on 465; TLS wins
    if tls is not None:
        mail_env.setenv("SMTP_TLS", tls)

    config = fallback_mail(Settings(environment="dev"))

    assert config is not None
    assert config.implicit_tls


def test_fallback_mail_refuses_a_login_in_clear_text(mail_env: pytest.MonkeyPatch) -> None:
    mail_env.setenv("SMTP_PORT", "25")
    mail_env.setenv("SMTP_USER", "planner@example.nl")

    with pytest.raises(ValueError, match="SMTP_STARTTLS or SMTP_TLS"):
        Settings(environment="dev")


def test_without_smtp_host_there_is_no_fallback_mail(mail_env: pytest.MonkeyPatch) -> None:
    mail_env.delenv("SMTP_HOST")

    assert fallback_mail(Settings(environment="dev")) is None


async def test_worker_writes_heartbeat_and_stops_on_event(settings: Settings) -> None:
    stop = asyncio.Event()
    clock = FixedClock()
    task = asyncio.create_task(run_worker(settings, stop, clock=clock))

    for _ in range(200):
        if settings.worker_heartbeat_path.exists():
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert settings.worker_heartbeat_path.read_text() == clock.now().isoformat()


async def test_worker_skips_heartbeat_when_database_is_down(
    unreachable_db_settings: Settings,
) -> None:
    broken = unreachable_db_settings
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(broken, stop, clock=FixedClock()))

    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert not broken.worker_heartbeat_path.exists()


def test_heartbeat_fresh_within_max_age(tmp_path: Path) -> None:
    clock = FixedClock()
    path = tmp_path / "hb"
    path.write_text(clock.now().isoformat())
    clock.advance(timedelta(seconds=59))

    assert heartbeat_is_fresh(path, max_age=timedelta(seconds=60), now=clock.now())


def test_heartbeat_stale_after_max_age(tmp_path: Path) -> None:
    clock = FixedClock()
    path = tmp_path / "hb"
    path.write_text(clock.now().isoformat())
    clock.advance(timedelta(seconds=61))

    assert not heartbeat_is_fresh(path, max_age=timedelta(seconds=60), now=clock.now())


@pytest.mark.parametrize("content", [None, "not a timestamp", "2026-01-05T09:00:00"])
def test_heartbeat_missing_or_invalid_is_not_fresh(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "hb"
    if content is not None:
        path.write_text(content)

    assert not heartbeat_is_fresh(path, max_age=timedelta(seconds=60), now=FixedClock().now())
