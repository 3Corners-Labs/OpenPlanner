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
from app.adapters.senders import ConfiguredSenders, connect_config, smtp_config
from app.adapters.smtp import SmtpConfig, SmtpMailSender
from app.adapters.webex_connect_sms import WebexConnectSmsSender
from app.domain.channel_settings import SmsSettings, SmtpSecurity, SmtpSettings
from app.domain.channels import ChannelKind
from tests.support.database import migrated_database

AT = datetime(2026, 1, 1, tzinfo=UTC)
FALLBACK = SmtpConfig("mailpit", 1025, "noreply@example.nl")
SMTP = SmtpSettings("smtp.demo.nl", 465, SmtpSecurity.TLS, "user", "noreply@demo.nl", "Demo")
SMS = SmsSettings("https://api.us.webexconnect.io", "GemDemo", "32")


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
    sms = connect_config(SMS, "key")
    assert (sms.sender_id, sms.country_code, sms.service_key) == ("GemDemo", "32", "key")
