"""Storage of channel settings (secret sealed), reminder rules and group texts (ADR 0022)."""

from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.models import ChannelSettingsRow
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.channel_settings import SmsSettings, SmtpSecurity, SmtpSettings
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.errors import DomainError, ErrorCode

AT = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
SMTP = SmtpSettings("smtp.demo.nl", 587, SmtpSecurity.STARTTLS, "user", "noreply@demo.nl", "Demo")
SMS = SmsSettings("https://api.us.webexconnect.io", "Demo")
BOX = SecretBox(b"s" * 32)


async def _demo_ids(uow: SqlUnitOfWork) -> tuple[int, int]:
    async with uow.read() as tx:
        demo = await tx.get_organisation("demo")
        summary = await tx.get_group_summary("demo")
    assert demo is not None
    assert summary is not None
    return demo.id, summary.group.id


async def _both_channels(engine: AsyncEngine) -> tuple[SqlUnitOfWork, int]:
    """Seed the demo and store SMTP and SMS settings with their secrets."""
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine, BOX)
    org, _ = await _demo_ids(uow)
    async with uow.locked() as tx:
        await tx.save_channel_settings(org, SMTP, "wachtwoord", AT)
        await tx.save_channel_settings(org, SMS, "service-key", AT)
    return uow, org


async def test_channel_settings_round_trip_with_sealed_secret(engine: AsyncEngine) -> None:
    uow, org = await _both_channels(engine)
    async with uow.read() as tx:
        stored = await tx.get_channel_settings(org)
        secret = await tx.get_channel_secret(org, ChannelKind.SMS)
    assert [(s.channel, s.config, s.secret_set, s.updated_at) for s in stored] == [
        (ChannelKind.EMAIL, SMTP, True, AT),
        (ChannelKind.SMS, SMS, True, AT),
    ]
    assert secret == "service-key"

    async with AsyncSession(engine) as session:
        rows = list(await session.scalars(select(ChannelSettingsRow)))
    for row in rows:  # the secret is never stored in plain text
        assert b"wachtwoord" not in (row.secret_ciphertext or b"")
        assert "wachtwoord" not in row.settings
        assert "service-key" not in row.settings


async def test_saving_without_a_secret_keeps_it_and_delete_removes_all(
    engine: AsyncEngine,
) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine, BOX)
    org, _ = await _demo_ids(uow)
    relay = SmtpSettings("mailpit", 1025, SmtpSecurity.NONE, None, "noreply@demo.nl")

    async with uow.locked() as tx:
        await tx.save_channel_settings(org, SMTP, "wachtwoord", AT)
        await tx.save_channel_settings(org, relay, None, AT)
        assert await tx.get_channel_secret(org, ChannelKind.EMAIL) == "wachtwoord"
        assert [s.config for s in await tx.get_channel_settings(org)] == [relay]
        assert await tx.get_channel_secret(org, ChannelKind.SMS) is None
        assert await tx.delete_channel_settings(org, ChannelKind.EMAIL)
        assert not await tx.delete_channel_settings(org, ChannelKind.EMAIL)
        assert await tx.get_channel_settings(org) == []


async def test_secrets_need_the_settings_key(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    org, _ = await _demo_ids(SqlUnitOfWork(engine, BOX))
    async with SqlUnitOfWork(engine, BOX).locked() as tx:
        await tx.save_channel_settings(org, SMS, "service-key", AT)

    keyless = SqlUnitOfWork(engine)
    with pytest.raises(DomainError) as error:
        async with keyless.locked() as tx:
            await tx.save_channel_settings(org, SMS, "other", AT)
    assert error.value.code is ErrorCode.INTEGRATION_NOT_READY
    with pytest.raises(DomainError):
        async with keyless.read() as tx:
            await tx.get_channel_secret(org, ChannelKind.SMS)


async def test_a_secret_cannot_be_moved_to_another_channel(engine: AsyncEngine) -> None:
    uow, org = await _both_channels(engine)
    async with AsyncSession(engine) as session, session.begin():
        email = await session.get(ChannelSettingsRow, (org, "email"))
        sms = await session.get(ChannelSettingsRow, (org, "sms"))
        assert email is not None
        assert sms is not None
        sms.secret_nonce, sms.secret_ciphertext = email.secret_nonce, email.secret_ciphertext

    with pytest.raises(InvalidTag):  # sealed under its own name
        async with uow.read() as tx:
            await tx.get_channel_secret(org, ChannelKind.SMS)


async def test_reminder_rules_add_and_delete(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    _, group = await _demo_ids(uow)
    async with uow.locked() as tx:
        before = await tx.get_reminder_rules(group)
        await tx.add_reminder_rule(group, ReminderRule(ChannelKind.SMS, 120))
    async with uow.locked() as tx:
        assert ReminderRule(ChannelKind.SMS, 120) in await tx.get_reminder_rules(group)
        assert await tx.delete_reminder_rule(group, ChannelKind.SMS, 120)
        assert not await tx.delete_reminder_rule(group, ChannelKind.SMS, 120)
        assert await tx.get_reminder_rules(group) == before


async def test_group_texts_store_replace_and_delete(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    _, group = await _demo_ids(uow)
    async with uow.locked() as tx:
        await tx.save_group_text(group, "nl", "message.reminder.sms", "Eerste")
        await tx.save_group_text(group, "nl", "message.reminder.sms", "Tweede")
        await tx.save_group_text(group, "en", "message.reminder.sms", "Second")
    async with uow.locked() as tx:
        assert await tx.get_group_texts(group) == {
            ("nl", "message.reminder.sms"): "Tweede",
            ("en", "message.reminder.sms"): "Second",
        }
        await tx.save_group_text(group, "nl", "message.reminder.sms", None)
        assert await tx.get_group_texts(group) == {("en", "message.reminder.sms"): "Second"}
