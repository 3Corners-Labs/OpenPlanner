"""An organisation's mail and SMS channels in a configuration document: read and write."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.config_channels import WRITERS, read_channels
from app.adapters.db.models import ChannelSettingsRow, OrganisationRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.channels import ChannelKind
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_values import Secret, Value
from app.domain.errors import DomainError, ErrorCode
from tests.support.config_changes import apply_changes, planned
from tests.support.config_roundtrip import document_of, organisations, plan_against

BOX = SecretBox(b"k" * 32)
EMAIL: dict[str, Value] = {
    "host": "smtp.voorbeeld.nl",
    "port": 587,
    "security": "starttls",
    "username": "afspraken",
    "sender_address": "afspraken@voorbeeld.nl",
    "sender_name": "Acme",
    "reply_to_address": None,
    "dkim_selector": None,
    "secret": Secret("mail-password"),
}
SMS: dict[str, Value] = {
    "provider": "webex_connect",
    "api_base_url": "https://api.us.webexconnect.io",
    "sender_id": "Acme",
    "country_code": "31",
    "secret": Secret("sms-api-key"),
}


def change(
    kind: str, action: ChangeAction, values: dict[str, Value], *fields: str, org: str = "acme"
) -> Change:
    return planned(kind, (org,), action, values, fields or None, key=(), organisation=org)


async def apply(engine: AsyncEngine, box: SecretBox | None, *changes: Change) -> None:
    await apply_changes(engine, WRITERS, box, *changes)


async def read(engine: AsyncEngine, box: SecretBox | None = BOX) -> dict[str, tuple]:  # type: ignore[type-arg]
    async with AsyncSession(engine) as session:
        return (await read_channels(session, box)).get("acme", {})


@pytest.fixture
async def installation(engine: AsyncEngine) -> AsyncEngine:
    async with AsyncSession(engine) as session, session.begin():
        session.add(OrganisationRow(name="Acme", slug="acme"))
    return engine


async def seed(engine: AsyncEngine) -> None:
    await apply(
        engine,
        BOX,
        change("email_channel", ChangeAction.CREATE, EMAIL),
        change("sms_channel", ChangeAction.CREATE, SMS),
    )


async def test_the_reader_returns_each_configured_channel_with_its_opened_secret(
    installation: AsyncEngine,
) -> None:
    assert await read(installation) == {}
    await seed(installation)

    found = await read(installation)

    (email,) = found["email"]
    (sms,) = found["sms"]
    assert (email.kind, email.key) == ("email_channel", ())
    assert email.fields == EMAIL
    assert sms.fields == SMS


async def test_only_a_configured_channel_is_read(installation: AsyncEngine) -> None:
    await apply(installation, BOX, change("sms_channel", ChangeAction.CREATE, SMS))

    assert list(await read(installation)) == ["sms"]


async def test_a_reader_without_a_settings_key_shows_no_secret(installation: AsyncEngine) -> None:
    await seed(installation)

    found = await read(installation, None)

    assert found["email"][0].fields["secret"] is None
    assert found["email"][0].fields["host"] == "smtp.voorbeeld.nl"


async def test_the_secret_opens_for_the_sender_of_the_worker(installation: AsyncEngine) -> None:
    await seed(installation)
    uow = SqlUnitOfWork(installation, BOX)

    async with uow.read() as tx:
        async with AsyncSession(installation) as session:
            organisation = await session.scalar(select(OrganisationRow.id))
        assert organisation is not None
        email = await tx.get_channel_secret(organisation, ChannelKind.EMAIL)
        sms = await tx.get_channel_secret(organisation, ChannelKind.SMS)
        stored = await tx.get_channel_settings(organisation)

    assert (email, sms) == ("mail-password", "sms-api-key")
    assert [s.config.channel for s in stored] == [ChannelKind.EMAIL, ChannelKind.SMS]


@pytest.mark.parametrize("secrets", [True, False])
async def test_a_document_of_what_was_read_changes_nothing(
    installation: AsyncEngine, secrets: bool
) -> None:
    await seed(installation)
    tree = organisations(acme=await read(installation))

    plan = plan_against(document_of(tree, secrets=secrets), tree)

    assert plan.refusals == ()
    assert plan.changes == ()


async def test_an_update_rewrites_the_settings_and_keeps_the_secret_unless_named(
    installation: AsyncEngine,
) -> None:
    await seed(installation)
    moved = {**EMAIL, "host": "smtp2.voorbeeld.nl", "port": 465, "security": "tls"}

    await apply(
        installation,
        BOX,
        change("email_channel", ChangeAction.UPDATE, moved, "host", "port", "security"),
    )

    email = (await read(installation))["email"][0]
    assert (email.fields["host"], email.fields["port"], email.fields["security"]) == (
        "smtp2.voorbeeld.nl",
        465,
        "tls",
    )
    assert email.fields["secret"] == Secret("mail-password")
    renewed = {**moved, "secret": Secret("new-password")}
    await apply(installation, BOX, change("email_channel", ChangeAction.UPDATE, renewed, "secret"))
    assert (await read(installation))["email"][0].fields["secret"] == Secret("new-password")
    cleared = {**moved, "secret": None}
    await apply(installation, BOX, change("email_channel", ChangeAction.UPDATE, cleared, "secret"))
    assert (await read(installation))["email"][0].fields["secret"] is None


async def test_a_delete_removes_one_channel_and_is_quiet_when_it_is_gone(
    installation: AsyncEngine,
) -> None:
    await seed(installation)

    await apply(installation, BOX, change("sms_channel", ChangeAction.DELETE, {}))
    await apply(installation, BOX, change("sms_channel", ChangeAction.DELETE, {}))

    assert list(await read(installation)) == ["email"]


async def test_a_secret_without_a_settings_key_is_refused_and_nothing_is_written(
    installation: AsyncEngine,
) -> None:
    with pytest.raises(DomainError) as refused:
        await apply(installation, None, change("email_channel", ChangeAction.CREATE, EMAIL))

    assert refused.value.code is ErrorCode.INTEGRATION_NOT_READY
    async with AsyncSession(installation) as session:
        assert await session.scalar(select(ChannelSettingsRow.channel)) is None


async def test_a_channel_without_a_secret_needs_no_settings_key(installation: AsyncEngine) -> None:
    relay = {**EMAIL, "security": "none", "port": 25, "username": None, "secret": None}

    await apply(installation, None, change("email_channel", ChangeAction.CREATE, relay))

    assert (await read(installation, None))["email"][0].fields["secret"] is None


async def test_an_unknown_organisation_or_a_second_create_is_refused(
    installation: AsyncEngine,
) -> None:
    with pytest.raises(DomainError) as unknown:
        await apply(
            installation, BOX, change("email_channel", ChangeAction.CREATE, EMAIL, org="zzz")
        )
    await seed(installation)
    with pytest.raises(DomainError) as twice:
        await apply(installation, BOX, change("email_channel", ChangeAction.CREATE, EMAIL))

    assert unknown.value.code is ErrorCode.ORGANISATION_NOT_FOUND
    assert twice.value.code is ErrorCode.CONFIGURATION_OBJECT_EXISTS


async def test_an_unopened_secret_is_refused(installation: AsyncEngine) -> None:
    from app.domain.configuration_values import Sealed

    with pytest.raises(DomainError) as refused:
        await apply(
            installation,
            BOX,
            change("email_channel", ChangeAction.CREATE, {**EMAIL, "secret": Sealed("c2Vj")}),
        )

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION
