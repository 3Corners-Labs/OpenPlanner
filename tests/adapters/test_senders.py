"""ConfiguredSenders: the sender per organisation and channel comes from the stored settings."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.crypto import SecretBox
from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.senders import (
    SMS_GATEWAYS,
    ConfiguredSenders,
    fallback_mail,
    sms_config,
    smtp_config,
)
from app.adapters.smtp import SmtpConfig, SmtpMailSender
from app.adapters.webex_connect_sms import WebexConnectSmsSender
from app.config import Settings
from app.domain.channel_settings import SmsProvider, SmsSettings, SmtpSecurity, SmtpSettings
from app.domain.channels import ChannelKind
from tests.support.database import migrated_database

AT = datetime(2026, 1, 1, tzinfo=UTC)
FALLBACK = SmtpConfig("mailpit", 1025, "noreply@example.nl")
SMTP = SmtpSettings("smtp.demo.nl", 465, SmtpSecurity.TLS, "user", "noreply@demo.nl", "Demo")
SMS = SmsSettings(SmsProvider.WEBEX_CONNECT, "https://api.us.webexconnect.io", "GemDemo", "32")


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "senders")


@pytest.fixture
async def uow(database_url: str) -> AsyncIterator[SqlUnitOfWork]:
    engine: AsyncEngine = create_engine(database_url)
    await seed_demo(engine)
    yield SqlUnitOfWork(engine, SecretBox(b"s" * 32))
    await engine.dispose()


async def _org(uow: SqlUnitOfWork) -> int:
    async with uow.read() as tx:
        demo = await tx.get_organisation("demo")
    assert demo is not None
    return demo.id


async def test_without_settings_email_falls_back_and_sms_has_none(uow: SqlUnitOfWork) -> None:
    org = await _org(uow)
    async with httpx.AsyncClient() as http:
        assert isinstance(
            await ConfiguredSenders(uow, http, FALLBACK).sender(org, ChannelKind.EMAIL),
            SmtpMailSender,
        )
        assert await ConfiguredSenders(uow, http, None).sender(org, ChannelKind.EMAIL) is None
        assert await ConfiguredSenders(uow, http, FALLBACK).sender(org, ChannelKind.SMS) is None


async def test_stored_settings_build_their_sender(uow: SqlUnitOfWork) -> None:
    org = await _org(uow)
    async with uow.locked() as tx:
        await tx.save_channel_settings(org, SMTP, "pw", AT)
        await tx.save_channel_settings(org, SMS, "key", AT)
    async with httpx.AsyncClient() as http:
        senders = ConfiguredSenders(uow, http, FALLBACK)
        assert isinstance(await senders.sender(org, ChannelKind.EMAIL), SmtpMailSender)
        assert isinstance(await senders.sender(org, ChannelKind.SMS), WebexConnectSmsSender)


async def test_settings_without_their_secret_build_nothing(uow: SqlUnitOfWork) -> None:
    org = await _org(uow)
    async with uow.locked() as tx:
        await tx.save_channel_settings(org, SMS, None, AT)
    async with httpx.AsyncClient() as http:
        assert await ConfiguredSenders(uow, http, FALLBACK).sender(org, ChannelKind.SMS) is None


def test_adapter_configs_follow_the_settings() -> None:
    mail = smtp_config(SMTP, "pw")
    assert (mail.host, mail.port, mail.implicit_tls, mail.starttls) == (
        "smtp.demo.nl",
        465,
        True,
        False,
    )
    assert mail.sender == "Demo <noreply@demo.nl>"
    sms = sms_config(SMS, "key")
    assert (sms.sender_id, sms.country_code, sms.api_key) == ("GemDemo", "32", "key")


def test_every_sms_provider_has_an_adapter() -> None:
    assert set(SMS_GATEWAYS) == set(SmsProvider)


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
