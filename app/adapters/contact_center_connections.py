"""The organisations' contact-center connections, sealed in one record each (ADR 0032 point 9).

One AES-256-GCM record per organisation in ``integration_credentials`` under
``contact-center:{organisation id}`` (``contact_center_credential_name``), holding the kind,
the API host, the client id and the three secrets: client secret, refresh token and the
webhook secret this module generates, and the networks the platform's webhooks may come from
(``allowed_networks``, not secret, ADR 0124 point 3; a record without the field reads as an
empty list, so records made before it keep working; a field that cannot be read reads as an
unreadable list, never as an empty one) and whether the address handles its events
(``inbound_enabled``, the pause of ADR 0124 point 9; a record without the field reads as on, and
only a stored ``False`` pauses). Nothing of it is readable in the portal:
``get`` returns ``OrganisationConnection``, which says only whether a secret is set.
``webhook_secret`` is for the webhook route only, outside the domain.
"""

import secrets as random
from dataclasses import dataclass, field
from datetime import datetime

from app.adapters.db.credentials import Secrets
from app.adapters.db.record_names import contact_center_credential_name, organisation_tokens_name
from app.domain.contact_center import ConnectionChange, ContactCenterKind, OrganisationConnection
from app.domain.networks import NetworkList
from app.domain.source_networks import read_stored_networks

WEBHOOK_SECRET_BYTES = 32
"""Random bytes of a generated webhook secret: 43 URL-safe characters (WxCC wants 32–256)."""


def new_webhook_secret() -> str:
    """Return a fresh webhook secret (URL-safe, never typed or shown)."""
    return random.token_urlsafe(WEBHOOK_SECRET_BYTES)


@dataclass(frozen=True)
class ConnectionCredentials:
    """An organisation's connection with its OAuth secrets, for its dispatcher only.

    Attributes:
        kind: The platform.
        api_base_url: The platform's API host.
        client_id: The OAuth client id.
        client_secret: The client secret; ``""`` when none is stored.
        refresh_token: The refresh token; ``""`` when none is stored.
    """

    kind: ContactCenterKind
    api_base_url: str
    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)

    @property
    def complete(self) -> bool:
        """Whether tokens can be had: both OAuth secrets are stored."""
        return bool(self.client_secret and self.refresh_token)


class SealedConnections:
    """``ContactCenterConnections`` on the encrypted credential store.

    Args:
        store: The encrypted credential store (``CredentialStore``).
    """

    def __init__(self, store: Secrets) -> None:
        """Store the credential store."""
        self._store = store

    async def _record(self, organisation_id: int) -> dict[str, object] | None:
        """Return the organisation's stored record, or ``None``."""
        return await self._store.get(contact_center_credential_name(organisation_id))

    async def get(self, organisation_id: int) -> OrganisationConnection | None:
        """Return the connection without its secrets, or ``None``."""
        record = await self._record(organisation_id)
        if record is None:
            return None
        rotated = record.get("webhook_secret_at")
        networks = (
            read_stored_networks(record["allowed_networks"])
            if "allowed_networks" in record
            else NetworkList()
        )
        return OrganisationConnection(
            ContactCenterKind(str(record["kind"])),
            str(record["api_base_url"]),
            str(record["client_id"]),
            has_client_secret=bool(record.get("client_secret")),
            has_refresh_token=bool(record.get("refresh_token")),
            webhook_secret_at=datetime.fromisoformat(rotated) if isinstance(rotated, str) else None,
            allowed_networks=networks,
            inbound_enabled=record.get("inbound_enabled") is not False,
        )

    async def save(self, organisation_id: int, change: ConnectionChange, now: datetime) -> None:
        """Store the connection; a ``None`` secret keeps the stored one.

        A new connection gets its webhook secret at once, so its address works from the start;
        a changed API host or client id gets a new one, so a subscription the previous tenant
        still holds no longer verifies (CC-4d). Changing the allowed networks alone touches
        neither the secret nor anything else of the record.
        """
        record = dict(await self._record(organisation_id) or {})
        moved = (record.get("api_base_url"), record.get("client_id")) != (
            change.api_base_url,
            change.client_id,
        )
        record |= {
            "kind": change.kind.value,
            "api_base_url": change.api_base_url,
            "client_id": change.client_id,
        }
        if change.allowed_networks is not None:
            record["allowed_networks"] = list(change.allowed_networks)
        if change.client_secret is not None:
            record["client_secret"] = change.client_secret
        if change.refresh_token is not None:
            record["refresh_token"] = change.refresh_token
        if moved or not record.get("webhook_secret"):
            # Another tenant (host or client): the old tenant's subscription must stop verifying.
            record |= {"webhook_secret": new_webhook_secret(), "webhook_secret_at": now.isoformat()}
        await self._store.put(contact_center_credential_name(organisation_id), record, now)

    async def set_inbound_enabled(self, organisation_id: int, enabled: bool, now: datetime) -> None:
        """Pause or resume the address (ADR 0124 point 9): that one field, nothing else.

        Nothing happens for an organisation whose record went away in the meantime, as for
        ``rotate_webhook_secret``.
        """
        record = await self._record(organisation_id)
        if record is None:
            return
        record["inbound_enabled"] = enabled
        await self._store.put(contact_center_credential_name(organisation_id), record, now)

    async def rotate_webhook_secret(self, organisation_id: int, now: datetime) -> None:
        """Replace the webhook secret of an existing connection; nothing without one."""
        record = await self._record(organisation_id)
        if record is None:
            return
        record |= {"webhook_secret": new_webhook_secret(), "webhook_secret_at": now.isoformat()}
        await self._store.put(contact_center_credential_name(organisation_id), record, now)

    async def delete(self, organisation_id: int) -> None:
        """Remove the organisation's connection, its secrets and the access tokens it was given."""
        await self._store.delete(contact_center_credential_name(organisation_id))
        await self._store.delete(organisation_tokens_name(organisation_id))

    async def credentials(self, organisation_id: int) -> ConnectionCredentials | None:
        """Return what the dispatcher needs to act for the organisation; for the adapter only."""
        record = await self._record(organisation_id)
        if record is None:
            return None
        return ConnectionCredentials(
            ContactCenterKind(str(record["kind"])),
            str(record["api_base_url"]),
            str(record["client_id"]),
            str(record.get("client_secret") or ""),
            str(record.get("refresh_token") or ""),
        )

    async def webhook_secret(self, organisation_id: int, kind: ContactCenterKind) -> str | None:
        """Return the webhook secret when the organisation is connected to ``kind``; else ``None``.

        For the webhook route only (ADR 0032 point 10); never shown in the portal.
        """
        record = await self._record(organisation_id)
        if record is None or record.get("kind") != kind.value:
            return None
        secret = record.get("webhook_secret")
        return secret if isinstance(secret, str) and secret else None
