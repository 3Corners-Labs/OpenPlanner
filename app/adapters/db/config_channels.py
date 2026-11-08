"""An organisation's mail server and SMS gateway in a configuration document: read and write.

The adapter side of ``app.domain.configuration_v1_channels`` (ADR 0099 step 2). Both channels
live in ``channel_settings``: the non-secret settings as JSON (the typed fields of
``SmtpSettings`` and ``SmsSettings``), the password or API key sealed under
``channel:<organisation id>:<channel>`` (ADR 0008), so the sender built from it by the worker
opens it. Neither the settings version nor an audit event is written here.
"""

import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.config_login import Writer, organisation_id, seal
from app.adapters.db.models import ChannelSettingsRow, OrganisationRow
from app.domain import configuration_v1_names as names
from app.domain.channel_settings import (
    SmsProvider,
    SmsSettings,
    SmtpSecurity,
    SmtpSettings,
)
from app.domain.channels import ChannelKind
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Action, Entity
from app.domain.configuration_values import Secret, Value
from app.domain.errors import DomainError, ErrorCode

_SETTINGS_FIELDS: dict[ChannelKind, tuple[str, ...]] = {
    ChannelKind.EMAIL: (
        "host",
        "port",
        "security",
        "username",
        "sender_address",
        "sender_name",
        "reply_to_address",
        "dkim_selector",
    ),
    ChannelKind.SMS: ("provider", "api_base_url", "sender_id", "country_code"),
}
_KINDS = {ChannelKind.EMAIL: names.EMAIL_CHANNEL_KIND, ChannelKind.SMS: names.SMS_CHANNEL_KIND}
_CHANNEL_OF_KIND = {kind: channel for channel, kind in _KINDS.items()}


def _secret_name(organisation: int, channel: ChannelKind) -> str:
    """Return the record name a channel secret is sealed under (associated data, ADR 0008)."""
    return f"channel:{organisation}:{channel.value}"


async def read_channels(
    session: AsyncSession, box: SecretBox | None
) -> dict[str, dict[str, tuple[Entity, ...]]]:
    """Return each organisation's configured channels as ``{"email": …, "sms": …}`` by slug."""
    rows = (
        await session.execute(
            select(ChannelSettingsRow, OrganisationRow.slug)
            .join(OrganisationRow, OrganisationRow.id == ChannelSettingsRow.organisation_id)
            .order_by(OrganisationRow.slug, ChannelSettingsRow.channel)
        )
    ).all()
    found: dict[str, dict[str, tuple[Entity, ...]]] = {}
    for row, slug in rows:
        channel = ChannelKind(row.channel)
        stored = json.loads(row.settings)
        fields: dict[str, Value] = {name: stored.get(name) for name in _SETTINGS_FIELDS[channel]}
        fields["secret"] = (
            Secret(
                box.open(
                    _secret_name(row.organisation_id, channel),
                    row.secret_nonce,
                    row.secret_ciphertext,
                ).decode()
            )
            if box is not None
            and row.secret_nonce is not None
            and row.secret_ciphertext is not None
            else None
        )
        found.setdefault(slug, {})[channel.value] = (
            Entity(_KINDS[channel], (), Action.UPSERT, fields),
        )
    return found


def _settings_json(channel: ChannelKind, values: Mapping[str, Value]) -> str:
    """Return the settings JSON the fields state, built through the validating class."""
    if channel is ChannelKind.EMAIL:
        port = values["port"]
        config: SmtpSettings | SmsSettings = SmtpSettings(
            host=str(values["host"]),
            port=port if isinstance(port, int) else 0,
            security=SmtpSecurity(str(values["security"])),
            username=_optional(values, "username"),
            sender_address=str(values["sender_address"]),
            sender_name=_optional(values, "sender_name"),
            reply_to_address=_optional(values, "reply_to_address"),
            dkim_selector=_optional(values, "dkim_selector"),
        )
    else:
        config = SmsSettings(
            provider=SmsProvider(str(values["provider"])),
            api_base_url=str(values["api_base_url"]),
            sender_id=str(values["sender_id"]),
            country_code=str(values["country_code"]),
        )
    return json.dumps(asdict(config), sort_keys=True)


def _optional(values: Mapping[str, Value], name: str) -> str | None:
    """Return an optional text field, ``None`` when it is absent."""
    value = values.get(name)
    return None if value is None else str(value)


async def write_channel(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Create, update or delete the mail or SMS channel of an organisation.

    Raises:
        DomainError: ``invalid_configuration`` for an unknown organisation or an unopened
            secret, ``integration_not_ready`` for a secret and no settings key.
    """
    channel = _CHANNEL_OF_KIND[change.kind]
    organisation = await organisation_id(session, change.address[0])
    row = await session.get(ChannelSettingsRow, (organisation, channel.value))
    if change.action is ChangeAction.DELETE:
        if row is not None:
            await session.delete(row)
            await session.flush()
        return
    create = row is None
    if row is None:
        row = ChannelSettingsRow(organisation_id=organisation, channel=channel.value)
        session.add(row)
    elif change.action is ChangeAction.CREATE:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_EXISTS)
    touched = set(_SETTINGS_FIELDS[channel]) & set(change.fields)
    if create or touched:
        row.settings = _settings_json(channel, change.values)
    if create or "secret" in change.fields:
        value = change.values.get("secret")
        if value is None:
            row.secret_nonce = row.secret_ciphertext = None
        else:
            row.secret_nonce, row.secret_ciphertext = seal(
                box, _secret_name(organisation, channel), value
            )
    row.updated_at = datetime.now(UTC)
    await session.flush()


WRITERS: Mapping[str, Writer] = {
    names.EMAIL_CHANNEL_KIND: write_channel,
    names.SMS_CHANNEL_KIND: write_channel,
}
"""The writers of this module, by the v1 kind they apply."""
