"""SQL queries of the machine API (ADR 0023): switches, API clients and access tokens.

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. Scopes and networks are stored as space- and newline-separated strings; the groups
a client is narrowed to live in ``api_client_groups``. Secrets and tokens arrive here already
hashed.
"""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    ApiAccessTokenRow,
    ApiClientGroupRow,
    ApiClientRow,
    ApiSettingsRow,
    GroupRow,
    OrganisationRow,
)
from app.domain.api_clients import AccessToken, ApiClient, ApiSettings
from app.domain.authorization import ApiScope, Scope

_SETTINGS_ID = 1


def _scopes(raw: str) -> frozenset[ApiScope]:
    """Parse stored scopes; a scope no longer known grants nothing."""
    known = {scope.value for scope in ApiScope}
    return frozenset(ApiScope(name) for name in raw.split() if name in known)


def _join_scopes(scopes: frozenset[ApiScope]) -> str:
    """Store scopes in a stable order."""
    return " ".join(sorted(scopes))


class ApiClientQueries:
    """The machine API's part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def get_api_settings(self) -> ApiSettings:
        """Return the switches (both off when never saved, ADR 0023)."""
        row = await self._session.get(ApiSettingsRow, _SETTINGS_ID)
        if row is None:
            return ApiSettings()
        return ApiSettings(row.enabled, row.docs_public, row.updated_at)

    async def save_api_settings(self, settings: ApiSettings) -> None:
        """Store the switches in the single settings row."""
        await self._session.merge(
            ApiSettingsRow(
                id=_SETTINGS_ID,
                enabled=settings.enabled,
                docs_public=settings.docs_public,
                updated_at=settings.updated_at,
            )
        )
        await self._session.flush()

    async def list_api_clients(self, scope: Scope) -> list[ApiClient]:
        """Return the API clients of the organisations inside ``scope``, ordered by name.

        API clients belong to an organisation, not to a group, so only the organisation part
        of the scope (or ``everything``) matches them.
        """
        query = select(ApiClientRow, OrganisationRow.slug).join(
            OrganisationRow, OrganisationRow.id == ApiClientRow.organisation_id
        )
        if not scope.everything:
            query = query.where(OrganisationRow.slug.in_(sorted(scope.organisations)))
        rows = (await self._session.execute(query.order_by(ApiClientRow.name))).all()
        return [await self._client(row, slug) for row, slug in rows]

    async def get_api_client(self, client_id: str) -> ApiClient | None:
        """Return the API client with the public ``client_id``."""
        found = (
            await self._session.execute(
                select(ApiClientRow, OrganisationRow.slug)
                .join(OrganisationRow, OrganisationRow.id == ApiClientRow.organisation_id)
                .where(ApiClientRow.client_id == client_id)
            )
        ).one_or_none()
        return await self._client(*found) if found else None

    async def add_api_client(self, client: ApiClient) -> ApiClient:
        """Store a new API client with its groups and return it with its id."""
        organisation_id = await self._session.scalar(
            select(OrganisationRow.id).where(OrganisationRow.slug == client.organisation)
        )
        if organisation_id is None:
            raise LookupError("unknown organisation")
        row = ApiClientRow(
            client_id=client.client_id,
            name=client.name,
            organisation_id=organisation_id,
            created_at=client.created_at,
        )
        self._fill(row, client)
        self._session.add(row)
        await self._session.flush()
        await self._save_groups(row.id, client.groups)
        return await self._client(row, client.organisation)

    async def save_api_client(self, client: ApiClient) -> None:
        """Store name, grant, switch, secret and last token time of an existing client."""
        row = await self._row(client.client_id)
        row.name = client.name
        self._fill(row, client)
        await self._session.flush()
        await self._save_groups(row.id, client.groups)

    async def add_access_token(self, token: AccessToken) -> None:
        """Store an issued access token (its hash only)."""
        row = await self._row(token.client_id)
        self._session.add(
            ApiAccessTokenRow(
                token_hash=token.token_hash,
                client_pk=row.id,
                scopes=_join_scopes(token.scopes),
                issued_at=token.issued_at,
                expires_at=token.expires_at,
            )
        )
        await self._session.flush()

    async def get_access_token(self, token_hash: str) -> AccessToken | None:
        """Return the stored access token with ``token_hash``."""
        found = (
            await self._session.execute(
                select(ApiAccessTokenRow, ApiClientRow.client_id)
                .join(ApiClientRow, ApiClientRow.id == ApiAccessTokenRow.client_pk)
                .where(ApiAccessTokenRow.token_hash == token_hash)
            )
        ).one_or_none()
        if found is None:
            return None
        row, client_id = found
        return AccessToken(
            row.token_hash, client_id, _scopes(row.scopes), row.issued_at, row.expires_at
        )

    async def revoke_access_tokens(self, client_id: str) -> int:
        """Delete every access token of the client; return how many there were."""
        row = await self._row(client_id)
        result = await self._session.execute(
            delete(ApiAccessTokenRow).where(ApiAccessTokenRow.client_pk == row.id)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def prune_access_tokens(self, client_id: str, now: datetime, keep: int) -> None:
        """Delete expired tokens of every client, and all but the newest ``keep`` of this one."""
        await self._session.execute(
            delete(ApiAccessTokenRow).where(ApiAccessTokenRow.expires_at <= now)
        )
        row = await self._row(client_id)
        newest = (
            select(ApiAccessTokenRow.token_hash)
            .where(ApiAccessTokenRow.client_pk == row.id)
            .order_by(ApiAccessTokenRow.issued_at.desc(), ApiAccessTokenRow.token_hash)
            .limit(keep)
        )
        await self._session.execute(
            delete(ApiAccessTokenRow).where(
                ApiAccessTokenRow.client_pk == row.id,
                ApiAccessTokenRow.token_hash.not_in(newest),
            )
        )

    async def _row(self, client_id: str) -> ApiClientRow:
        """Return the row of an existing client; a missing one is a programming error."""
        row = await self._session.scalar(
            select(ApiClientRow).where(ApiClientRow.client_id == client_id)
        )
        if row is None:
            raise LookupError("unknown API client")
        return row

    @staticmethod
    def _fill(row: ApiClientRow, client: ApiClient) -> None:
        """Copy the changeable fields of ``client`` onto ``row``."""
        row.scopes = _join_scopes(client.scopes)
        row.networks = "\n".join(client.networks)
        row.enabled = client.enabled
        row.secret_hash = client.secret_hash
        row.secret_expires_at = client.secret_expires_at
        row.last_token_at = client.last_token_at

    async def _save_groups(self, client_pk: int, slugs: frozenset[str]) -> None:
        """Replace the groups the client is narrowed to."""
        await self._session.execute(
            delete(ApiClientGroupRow).where(ApiClientGroupRow.client_pk == client_pk)
        )
        if not slugs:
            return
        group_ids = await self._session.scalars(
            select(GroupRow.id).where(GroupRow.slug.in_(sorted(slugs)))
        )
        ids = list(group_ids)
        if len(ids) != len(slugs):
            raise LookupError("unknown group")
        self._session.add_all(ApiClientGroupRow(client_pk=client_pk, group_id=i) for i in ids)
        await self._session.flush()

    async def _client(self, row: ApiClientRow, organisation: str) -> ApiClient:
        """Map a client row, with its group slugs, to the domain."""
        groups = await self._session.scalars(
            select(GroupRow.slug)
            .join(ApiClientGroupRow, ApiClientGroupRow.group_id == GroupRow.id)
            .where(ApiClientGroupRow.client_pk == row.id)
        )
        return ApiClient(
            id=row.id,
            client_id=row.client_id,
            name=row.name,
            organisation=organisation,
            groups=frozenset(groups),
            scopes=_scopes(row.scopes),
            networks=tuple(n for n in row.networks.split("\n") if n),
            enabled=row.enabled,
            secret_hash=row.secret_hash,
            secret_expires_at=row.secret_expires_at,
            created_at=row.created_at,
            last_token_at=row.last_token_at,
        )
