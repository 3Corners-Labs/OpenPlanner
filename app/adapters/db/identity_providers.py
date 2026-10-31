"""SQL queries of the organisation identity providers (ADR 0083 step 2).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction. The client
secret and every private key are sealed with the ``SecretBox`` (ADR 0008) under a name that
binds them to their provider and key id, so a ciphertext cannot be moved to another record.
The domain never sees them: it stores a secret and learns only that one is set.

``ProviderCredentialStore`` is the adapter side of the same tables: the OIDC adapter of a
provider is built from it (``app.adapters.idp_registry``), outside any domain transaction.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.models import IdentityProviderKeyRow, IdentityProviderRow, OrganisationRow
from app.domain.authorization import Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.identity_providers import (
    KEY_OVERLAP,
    ClientAuth,
    GeneratedKey,
    OrganisationProvider,
    SigningKey,
)


def _secret_name(key: str) -> str:
    """Return the record name a client secret is sealed under (associated data)."""
    return f"idp:{key}:secret"


def _key_name(key: str, kid: str) -> str:
    """Return the record name a private key is sealed under (associated data)."""
    return f"idp:{key}:key:{kid}"


def _provider(row: IdentityProviderRow, organisation: str) -> OrganisationProvider:
    """Map a row to the domain object; never the sealed secret."""
    return OrganisationProvider(
        id=row.id,
        organisation=organisation,
        key=row.key,
        name=row.name,
        discovery_url=row.discovery_url,
        issuer=row.issuer,
        client_id=row.client_id,
        client_auth=ClientAuth(row.client_auth),
        scopes=row.scopes,
        entitlement_claim=row.entitlement_claim,
        entitlement_prefix=row.entitlement_prefix,
        enabled=row.enabled,
        secret_set=row.secret_ciphertext is not None,
        created_at=row.created_at,
        updated_at=row.updated_at,
        checked_at=row.checked_at,
    )


def _signing_key(row: IdentityProviderKeyRow, provider_key: str) -> SigningKey:
    """Map a key row to its public side."""
    jwk: dict[str, str] = json.loads(row.public_jwk)
    return SigningKey(provider_key, row.kid, jwk, row.created_at, row.retired_at)


class IdentityProviderQueries:
    """The identity providers' part of ``SqlTransaction``; needs ``_session`` and ``_box``."""

    _session: AsyncSession
    _box: SecretBox | None

    async def list_identity_providers(self, scope: Scope) -> list[OrganisationProvider]:
        """Return the providers of the organisations inside ``scope``."""
        query = select(IdentityProviderRow, OrganisationRow.slug).join(
            OrganisationRow, OrganisationRow.id == IdentityProviderRow.organisation_id
        )
        if not scope.everything:
            query = query.where(OrganisationRow.slug.in_(sorted(scope.organisations)))
        rows = (
            await self._session.execute(
                query.order_by(OrganisationRow.slug, IdentityProviderRow.key)
            )
        ).all()
        return [_provider(row, slug) for row, slug in rows]

    async def get_identity_provider(self, key: str) -> OrganisationProvider | None:
        """Return the provider with ``key``."""
        found = (
            await self._session.execute(
                select(IdentityProviderRow, OrganisationRow.slug)
                .join(OrganisationRow, OrganisationRow.id == IdentityProviderRow.organisation_id)
                .where(IdentityProviderRow.key == key)
            )
        ).one_or_none()
        return _provider(*found) if found else None

    async def add_identity_provider(
        self, provider: OrganisationProvider, secret: str | None
    ) -> OrganisationProvider:
        """Store a new provider; the secret is sealed.

        Raises:
            LookupError: The organisation does not exist.
            DomainError: ``integration_not_ready`` when a secret is given without a box.
        """
        organisation_id = await self._session.scalar(
            select(OrganisationRow.id).where(OrganisationRow.slug == provider.organisation)
        )
        if organisation_id is None:
            raise LookupError("unknown organisation")
        row = IdentityProviderRow(
            organisation_id=organisation_id, key=provider.key, created_at=provider.created_at
        )
        self._fill_provider(row, provider)
        self._seal_secret(row, provider.key, secret)
        self._session.add(row)
        await self._session.flush()
        return _provider(row, provider.organisation)

    async def save_identity_provider(
        self, provider: OrganisationProvider, secret: str | None
    ) -> None:
        """Store the changes; ``secret=None`` keeps the stored one."""
        row = await self._provider_row(provider.key)
        self._fill_provider(row, provider)
        if secret is not None:
            self._seal_secret(row, provider.key, secret)
        await self._session.flush()

    async def delete_identity_provider(self, key: str) -> bool:
        """Remove the provider; its keys go with it (``ON DELETE CASCADE``)."""
        row = await self._session.scalar(
            select(IdentityProviderRow).where(IdentityProviderRow.key == key)
        )
        if row is None:
            return False
        await self._session.execute(
            delete(IdentityProviderKeyRow).where(IdentityProviderKeyRow.provider_id == row.id)
        )
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def list_signing_keys(self, provider_key: str) -> list[SigningKey]:
        """Return the provider's keys, newest first."""
        rows = (
            await self._session.scalars(
                select(IdentityProviderKeyRow)
                .join(
                    IdentityProviderRow,
                    IdentityProviderRow.id == IdentityProviderKeyRow.provider_id,
                )
                .where(IdentityProviderRow.key == provider_key)
                .order_by(IdentityProviderKeyRow.created_at.desc(), IdentityProviderKeyRow.kid)
            )
        ).all()
        return [_signing_key(row, provider_key) for row in rows]

    async def add_signing_key(self, provider_key: str, key: GeneratedKey, now: datetime) -> None:
        """Store a key pair; the private PEM is sealed under the provider and key id.

        Raises:
            DomainError: ``integration_not_ready`` without a box.
        """
        row = await self._provider_row(provider_key)
        nonce, ciphertext = self._seal(_key_name(provider_key, key.kid), key.private_pem)
        self._session.add(
            IdentityProviderKeyRow(
                kid=key.kid,
                provider_id=row.id,
                public_jwk=json.dumps(dict(key.public_jwk), sort_keys=True),
                key_nonce=nonce,
                key_ciphertext=ciphertext,
                created_at=now,
            )
        )
        await self._session.flush()

    async def retire_signing_keys(self, provider_key: str, *, keep: str, at: datetime) -> None:
        """Retire every current key of the provider except ``keep``."""
        row = await self._provider_row(provider_key)
        await self._session.execute(
            update(IdentityProviderKeyRow)
            .where(
                IdentityProviderKeyRow.provider_id == row.id,
                IdentityProviderKeyRow.kid != keep,
                IdentityProviderKeyRow.retired_at.is_(None),
            )
            .values(retired_at=at)
        )

    async def list_published_keys(self, now: datetime) -> list[SigningKey]:
        """Return the keys of every provider still inside the publication window."""
        rows = (
            await self._session.execute(
                select(IdentityProviderKeyRow, IdentityProviderRow.key)
                .join(
                    IdentityProviderRow,
                    IdentityProviderRow.id == IdentityProviderKeyRow.provider_id,
                )
                .where(
                    (IdentityProviderKeyRow.retired_at.is_(None))
                    | (IdentityProviderKeyRow.retired_at > now - KEY_OVERLAP)
                )
            )
        ).all()
        return [_signing_key(row, key) for row, key in rows]

    async def _provider_row(self, key: str) -> IdentityProviderRow:
        """Return the row of ``key``; it must exist."""
        row = await self._session.scalar(
            select(IdentityProviderRow).where(IdentityProviderRow.key == key)
        )
        if row is None:
            raise LookupError("unknown identity provider")
        return row

    @staticmethod
    def _fill_provider(row: IdentityProviderRow, provider: OrganisationProvider) -> None:
        """Copy the public configuration onto the row."""
        row.name = provider.name
        row.discovery_url = provider.discovery_url
        row.issuer = provider.issuer
        row.client_id = provider.client_id
        row.client_auth = provider.client_auth.value
        row.scopes = provider.scopes
        row.entitlement_claim = provider.entitlement_claim
        row.entitlement_prefix = provider.entitlement_prefix
        row.enabled = provider.enabled
        row.updated_at = provider.updated_at
        row.checked_at = provider.checked_at

    def _seal_secret(self, row: IdentityProviderRow, key: str, secret: str | None) -> None:
        """Seal ``secret`` onto the row, or clear it when ``None`` on a new row."""
        if secret is None:
            return
        row.secret_nonce, row.secret_ciphertext = self._seal(_secret_name(key), secret)

    def _seal(self, name: str, plaintext: str) -> tuple[bytes, bytes]:
        """Seal ``plaintext`` under ``name``.

        Raises:
            DomainError: ``integration_not_ready`` when no settings key is configured.
        """
        if self._box is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY)
        return self._box.seal(name, plaintext.encode())


@dataclass(frozen=True)
class ProviderCredentials:
    """What the OIDC adapter of one provider needs; read by the registry, never by the domain.

    Attributes:
        provider: The public registration.
        client_secret: The opened secret, for ``client_secret_basic``; ``None`` otherwise.
        kid: The current key id, for ``private_key_jwt``; ``None`` otherwise.
        private_pem: The opened private key; ``None`` without a current key.
    """

    provider: OrganisationProvider
    client_secret: str | None
    kid: str | None
    private_pem: str | None


class ProviderCredentialStore:
    """Opens a provider's sealed secret and current private key for its adapter.

    Args:
        engine: The database engine.
        box: Opens the sealed values; ``None`` when no settings key is configured, which
            leaves every provider without credentials.
    """

    def __init__(self, engine: AsyncEngine, box: SecretBox | None) -> None:
        """Store the collaborators."""
        self._engine = engine
        self._box = box

    async def credentials(self, key: str) -> ProviderCredentials | None:
        """Return the provider ``key`` with its opened credentials, or ``None`` if unknown."""
        async with AsyncSession(self._engine) as session:
            found = (
                await session.execute(
                    select(IdentityProviderRow, OrganisationRow.slug)
                    .join(
                        OrganisationRow, OrganisationRow.id == IdentityProviderRow.organisation_id
                    )
                    .where(IdentityProviderRow.key == key)
                )
            ).one_or_none()
            if found is None:
                return None
            row, slug = found
            current = await session.scalar(
                select(IdentityProviderKeyRow)
                .where(
                    IdentityProviderKeyRow.provider_id == row.id,
                    IdentityProviderKeyRow.retired_at.is_(None),
                )
                .order_by(IdentityProviderKeyRow.created_at.desc())
                .limit(1)
            )
        provider = _provider(row, slug)
        secret = kid = pem = None
        if self._box is not None:
            if row.secret_nonce is not None and row.secret_ciphertext is not None:
                secret = self._box.open(
                    _secret_name(key), row.secret_nonce, row.secret_ciphertext
                ).decode()
            if current is not None:
                kid = current.kid
                pem = self._box.open(
                    _key_name(key, current.kid), current.key_nonce, current.key_ciphertext
                ).decode()
        return ProviderCredentials(provider, secret, kid, pem)
