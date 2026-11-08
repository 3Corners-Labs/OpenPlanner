"""API clients in a configuration document: read from and written to the database (ADR 0099).

``read_api_clients`` turns every client row into the ``api_client`` object the version-1 format
describes. A client's secret is stored as a hash, not sealed, so its ``Secret`` holds the hash
(64 hex characters) and the document seals it like any secret. ``WRITERS`` applies one planned
change inside the caller's transaction, reusing the portal's queries; like the portal's own
update it revokes the client's tokens whenever more than the name changes. No value is logged.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.api_clients import ApiClientQueries
from app.adapters.db.config_group import Writer
from app.adapters.db.models import ApiClientGroupRow, ApiClientRow, GroupRow, OrganisationRow
from app.domain.api_clients import ApiClient
from app.domain.authorization import ApiScope
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_names import API_CLIENT_KIND
from app.domain.configuration_values import Json, Secret, Value
from app.domain.errors import DomainError, ErrorCode


class _Clients(ApiClientQueries):
    """The machine API's queries on a bare session."""

    def __init__(self, session: AsyncSession) -> None:
        """Keep the session the queries run on."""
        self._session = session


def _moment(value: datetime) -> str:
    """Write a stored time as the UTC ISO text a document carries."""
    return value.astimezone(UTC).isoformat()


def _texts(value: Value) -> list[str]:
    """Return a list field of the change as texts."""
    return [str(item) for item in cast("list[Json]", value or [])]


async def read_api_clients(session: AsyncSession, box: SecretBox | None) -> tuple[Entity, ...]:
    """Return every API client as a document object, oldest first.

    ``box`` is accepted for the contract of the readers; a client's secret is a hash and
    needs no key.
    """
    rows = (
        await session.execute(
            select(ApiClientRow, OrganisationRow.slug)
            .join(OrganisationRow, OrganisationRow.id == ApiClientRow.organisation_id)
            .order_by(ApiClientRow.id)
        )
    ).all()
    grants: dict[int, list[str]] = {}
    pairs = await session.execute(
        select(ApiClientGroupRow.client_pk, GroupRow.slug).join(
            GroupRow, GroupRow.id == ApiClientGroupRow.group_id
        )
    )
    for client_pk, slug in pairs.all():
        grants.setdefault(client_pk, []).append(slug)
    known = {scope.value for scope in ApiScope}
    clients: list[Entity] = []
    for row, organisation in rows:
        fields: dict[str, Value] = {
            "name": row.name,
            "organisation": organisation,
            "scopes": cast("list[Json]", sorted(s for s in row.scopes.split() if s in known)),
            "networks": cast("list[Json]", [n for n in row.networks.split("\n") if n]),
            "enabled": row.enabled,
            "groups": cast("list[Json]", sorted(grants.get(row.id, []))),
            "secret_hash": Secret(row.secret_hash),
            "secret_expires_at": _moment(row.secret_expires_at),
        }
        clients.append(Entity(API_CLIENT_KIND, (row.client_id,), fields=fields))
    return tuple(clients)


def _hash(value: Value) -> str:
    """Return the stored hash from an opened secret."""
    if not isinstance(value, Secret):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "secret_hash")
    return value.reveal()


def _scopes(value: Value) -> frozenset[ApiScope]:
    """Return the scopes a change holds."""
    return frozenset(ApiScope(name) for name in _texts(value))


async def write_api_client(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to an API client: create it, change the named fields, or delete it.

    Raises:
        DomainError: ``invalid_configuration`` when the organisation or a group does not exist,
            or the organisation of an existing client would change; ``api_client_not_found``.
    """
    queries = _Clients(session)
    client_id = change.key[0]
    if change.action is ChangeAction.DELETE:
        await queries.delete_api_client(client_id)
        return
    values = change.values
    if change.action is ChangeAction.CREATE:
        client = ApiClient(
            id=None,
            client_id=client_id,
            name=str(values["name"]),
            organisation=str(values["organisation"]),
            groups=frozenset(_texts(values.get("groups"))),
            scopes=_scopes(values.get("scopes")),
            networks=tuple(_texts(values.get("networks"))),
            enabled=bool(values.get("enabled", False)),
            secret_hash=_hash(values["secret_hash"]),
            secret_expires_at=datetime.fromisoformat(str(values["secret_expires_at"])),
            created_at=datetime.now(UTC),
        )
        try:
            await queries.add_api_client(client)
        except LookupError:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "organisation") from None
        return
    current = await queries.get_api_client(client_id)
    if current is None:
        raise DomainError(ErrorCode.API_CLIENT_NOT_FOUND, "client_id")
    named = set(change.fields)
    if "organisation" in named and values["organisation"] != current.organisation:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "organisation")
    updated = replace(
        current,
        name=str(values["name"]) if "name" in named else current.name,
        enabled=bool(values["enabled"]) if "enabled" in named else current.enabled,
        groups=frozenset(_texts(values["groups"])) if "groups" in named else current.groups,
        scopes=_scopes(values["scopes"]) if "scopes" in named else current.scopes,
        networks=tuple(_texts(values["networks"])) if "networks" in named else current.networks,
        secret_hash=_hash(values["secret_hash"]) if "secret_hash" in named else current.secret_hash,
        secret_expires_at=(
            datetime.fromisoformat(str(values["secret_expires_at"]))
            if "secret_expires_at" in named
            else current.secret_expires_at
        ),
    )
    try:
        await queries.save_api_client(updated)
    except LookupError:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "groups") from None
    if updated != current and named - {"name"}:
        await queries.revoke_access_tokens(client_id)


WRITERS: dict[str, Writer] = {API_CLIENT_KIND: write_api_client}
"""The writers of this module, by the name of the kind they apply."""
