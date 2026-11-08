"""Identity providers in a configuration document: read the current tree, apply a change.

The adapter side of ``app.domain.configuration_v1_login`` (ADR 0099 step 2). The reader builds
the providers of every organisation as ``Entity`` objects in the form the kinds expect, with a
set secret opened as ``Secret``; the writers apply one planned ``Change`` inside the caller's
transaction. Secrets are sealed under the record names of ADR 0008 (the same ones the admin
screens use, so the OIDC adapter opens them), a signing key's private part is sealed per
provider and key id, and a login domain created here starts ``pending`` with a new token: a
verification never travels from one installation to another. Neither the settings version nor
an audit event is written here; the caller does both once per apply.
"""

import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.config_common import (
    Writer,
    find_organisation_id,
    invalid,
    moment,
    opened,
    row_or_refuse,
    seal,
)
from app.adapters.db.identity_providers import key_name, secret_name
from app.adapters.db.models import (
    IdentityProviderDomainRow,
    IdentityProviderKeyRow,
    IdentityProviderRow,
    OrganisationRow,
)
from app.adapters.signing_keys import EcSigningKeys
from app.domain import configuration_v1_names as names
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Action, Entity
from app.domain.configuration_values import Secret, Value
from app.domain.errors import ErrorCode
from app.domain.identity_provider_service import TOKEN_BYTES
from app.domain.identity_providers import ClientAuth
from app.domain.login_domains import DomainState

_PROVIDER_FIELDS = (
    "name",
    "discovery_url",
    "client_id",
    "client_auth",
    "scopes",
    "entitlement_claim",
    "entitlement_prefix",
    "enabled",
    "mfa_policy",
    "mfa_amr_values",
    "mfa_acr_values",
)


async def read_identity_providers(
    session: AsyncSession, box: SecretBox | None
) -> dict[str, tuple[Entity, ...]]:
    """Return every organisation's providers (with their keys and domains) by its slug."""
    rows = (
        await session.execute(
            select(IdentityProviderRow, OrganisationRow.slug)
            .join(OrganisationRow, OrganisationRow.id == IdentityProviderRow.organisation_id)
            .order_by(OrganisationRow.slug, IdentityProviderRow.key)
        )
    ).all()
    provider_keys = {row.id: row.key for row, _ in rows}
    keys: dict[int, list[Entity]] = {}
    for key_row in await session.scalars(
        select(IdentityProviderKeyRow).order_by(
            IdentityProviderKeyRow.created_at, IdentityProviderKeyRow.kid
        )
    ):
        provider = provider_keys[key_row.provider_id]
        fields: dict[str, Value] = {
            "public_jwk": json.loads(key_row.public_jwk),
            "private_key": opened(
                box,
                key_name(provider, key_row.kid),
                key_row.key_nonce,
                key_row.key_ciphertext,
            ),
            "created_at": moment(key_row.created_at),
            "retired_at": moment(key_row.retired_at),
        }
        keys.setdefault(key_row.provider_id, []).append(
            Entity(names.PROVIDER_KEY_KIND, (key_row.kid,), Action.UPSERT, fields)
        )
    domains: dict[int, list[Entity]] = {}
    for domain_row in await session.scalars(
        select(IdentityProviderDomainRow).order_by(IdentityProviderDomainRow.domain)
    ):
        domains.setdefault(domain_row.provider_id, []).append(
            Entity(names.PROVIDER_DOMAIN_KIND, (domain_row.domain,))
        )
    found: dict[str, list[Entity]] = {}
    for row, slug in rows:
        fields = {name: getattr(row, name) for name in _PROVIDER_FIELDS}
        fields["client_secret"] = opened(
            box, secret_name(row.key), row.secret_nonce, row.secret_ciphertext
        )
        found.setdefault(slug, []).append(
            Entity(
                names.PROVIDER_KIND,
                (row.key,),
                Action.UPSERT,
                fields,
                {
                    "keys": tuple(keys.get(row.id, ())),
                    "domains": tuple(domains.get(row.id, ())),
                },
            )
        )
    return {slug: tuple(entities) for slug, entities in found.items()}


async def _provider_row(session: AsyncSession, key: str) -> IdentityProviderRow:
    """Return the provider ``key``.

    Raises:
        DomainError: ``invalid_configuration`` naming ``key`` when there is none.
    """
    return await row_or_refuse(
        session,
        select(IdentityProviderRow).where(IdentityProviderRow.key == key),
        ErrorCode.INVALID_CONFIGURATION,
        "key",
    )


def _write_client_secret(
    row: IdentityProviderRow, box: SecretBox | None, key: str, value: Value
) -> None:
    """Seal the client secret onto the row, or clear it for ``None``."""
    if value is None:
        row.secret_nonce = row.secret_ciphertext = None
        return
    row.secret_nonce, row.secret_ciphertext = seal(box, secret_name(key), value)


async def write_identity_provider(
    session: AsyncSession, box: SecretBox | None, change: Change
) -> None:
    """Create, update or delete a provider.

    A created provider carries the issuer the caller fetched from its discovery document in
    ``change.values["issuer"]`` (so does an update of ``discovery_url`` or ``client_auth``);
    ``ensure_signing_keys`` gives a ``private_key_jwt`` provider its key pair afterwards.

    Raises:
        DomainError: ``invalid_configuration`` (no issuer on create, an unknown provider or
            organisation), ``integration_not_ready`` (a secret and no settings key).
    """
    key = change.key[0]
    if change.action is ChangeAction.DELETE:
        row = await _provider_row(session, key)
        for table in (IdentityProviderKeyRow, IdentityProviderDomainRow):
            await session.execute(delete(table).where(table.provider_id == row.id))
        await session.delete(row)
        await session.flush()
        return
    now = datetime.now(UTC)
    issuer = change.values.get("issuer")
    if change.action is ChangeAction.CREATE:
        if not isinstance(issuer, str) or not issuer:
            raise invalid("issuer")
        row = IdentityProviderRow(
            organisation_id=await find_organisation_id(session, change.address[0]),
            key=key,
            issuer=issuer,
            created_at=now,
            updated_at=now,
            checked_at=now,
        )
        for name in _PROVIDER_FIELDS:
            setattr(row, name, change.values[name])
        _write_client_secret(row, box, key, change.values.get("client_secret"))
        session.add(row)
    else:
        row = await _provider_row(session, key)
        for name in change.fields:
            if name in _PROVIDER_FIELDS:
                setattr(row, name, change.values[name])
        if "client_secret" in change.fields:
            _write_client_secret(row, box, key, change.values.get("client_secret"))
        if isinstance(issuer, str) and issuer:
            row.issuer, row.checked_at = issuer, now
        row.updated_at = now
    await session.flush()


def _parsed_moment(value: Value, default: datetime | None) -> datetime | None:
    """Read a time field of a document (ISO text with a zone) back to a datetime."""
    if value is None or value == "":
        return default
    return datetime.fromisoformat(str(value)).astimezone(UTC)


async def write_signing_key(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Create, update or delete a signing key of a provider.

    Raises:
        DomainError: ``invalid_configuration`` (an unknown provider or key),
            ``integration_not_ready`` (a private key and no settings key).
    """
    provider, kid = change.address[-2], change.key[0]
    row = await session.get(IdentityProviderKeyRow, kid)
    if change.action is ChangeAction.DELETE:
        if row is not None:
            await session.delete(row)
            await session.flush()
        return
    values = change.values
    if change.action is ChangeAction.CREATE:
        provider_row = await _provider_row(session, provider)
        nonce, cipher = seal(box, key_name(provider, kid), values.get("private_key"))
        session.add(
            IdentityProviderKeyRow(
                kid=kid,
                provider_id=provider_row.id,
                public_jwk=json.dumps(values["public_jwk"], sort_keys=True),
                key_nonce=nonce,
                key_ciphertext=cipher,
                created_at=_parsed_moment(values.get("created_at"), datetime.now(UTC)),
                retired_at=_parsed_moment(values.get("retired_at"), None),
            )
        )
    else:
        if row is None:
            raise invalid("kid")
        if "public_jwk" in change.fields:
            row.public_jwk = json.dumps(values["public_jwk"], sort_keys=True)
        if "private_key" in change.fields:
            row.key_nonce, row.key_ciphertext = seal(
                box, key_name(provider, kid), values.get("private_key")
            )
        if "created_at" in change.fields:
            row.created_at = (
                _parsed_moment(values.get("created_at"), row.created_at) or row.created_at
            )
        if "retired_at" in change.fields:
            row.retired_at = _parsed_moment(values.get("retired_at"), None)
    await session.flush()


async def write_login_domain(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Create (pending, with a new token) or delete a claimed login domain.

    Raises:
        DomainError: ``invalid_configuration`` naming ``key`` for an unknown provider.
    """
    provider, domain = change.address[-2], change.key[0]
    provider_row = await _provider_row(session, provider)
    existing = await session.scalar(
        select(IdentityProviderDomainRow).where(
            IdentityProviderDomainRow.provider_id == provider_row.id,
            IdentityProviderDomainRow.domain == domain,
        )
    )
    if change.action is ChangeAction.DELETE:
        if existing is not None:
            await session.delete(existing)
    elif existing is None:
        session.add(
            IdentityProviderDomainRow(
                provider_id=provider_row.id,
                domain=domain,
                token=secrets.token_urlsafe(TOKEN_BYTES),
                state=DomainState.PENDING.value,
                created_at=datetime.now(UTC),
            )
        )
    await session.flush()


async def ensure_signing_keys(session: AsyncSession, box: SecretBox | None) -> list[str]:
    """Give every ``private_key_jwt`` provider without a key a new key pair; return their keys.

    The caller runs it after all changes of an apply, so a document that carries the keys keeps
    exactly those and one that does not gets what the portal would have generated.

    Raises:
        DomainError: ``integration_not_ready`` when a key is needed and there is no settings key.
    """
    created: list[str] = []
    providers = await session.scalars(
        select(IdentityProviderRow).where(
            IdentityProviderRow.client_auth == ClientAuth.PRIVATE_KEY_JWT.value
        )
    )
    for row in providers.all():
        has_key = await session.scalar(
            select(IdentityProviderKeyRow.kid).where(IdentityProviderKeyRow.provider_id == row.id)
        )
        if has_key is not None:
            continue
        generated = EcSigningKeys().generate()
        nonce, cipher = seal(box, key_name(row.key, generated.kid), Secret(generated.private_pem))
        session.add(
            IdentityProviderKeyRow(
                kid=generated.kid,
                provider_id=row.id,
                public_jwk=json.dumps(dict(generated.public_jwk), sort_keys=True),
                key_nonce=nonce,
                key_ciphertext=cipher,
                created_at=datetime.now(UTC),
            )
        )
        created.append(row.key)
    await session.flush()
    return created


WRITERS: Mapping[str, Writer] = {
    names.PROVIDER_KIND: write_identity_provider,
    names.PROVIDER_KEY_KIND: write_signing_key,
    names.PROVIDER_DOMAIN_KIND: write_login_domain,
}
"""The writers of this module, by the v1 kind they apply."""
