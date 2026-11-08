"""The organisations' contact-center connections, sealed in one record each (ADR 0032 point 9).

One AES-256-GCM record per organisation in ``integration_credentials`` under
``contact-center:{organisation id}`` (``contact_center_credential_name``), holding the kind,
the API host, the client id and the three secrets: client secret, refresh token and the
webhook secret this module generates. Nothing of it is readable in the portal: ``get`` returns
``OrganisationConnection``, which says only whether a secret is set. ``webhook_secret`` is for
the webhook route only, outside the domain.
"""

import secrets as random
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.alert_senders import Secrets
from app.adapters.db.credentials import CredentialStore
from app.adapters.db.uow import contact_center_credential_name
from app.adapters.integrations import secret_box
from app.config import Settings
from app.domain.contact_center import ConnectionChange, ContactCenterKind, OrganisationConnection

WEBHOOK_SECRET_BYTES = 32
"""Random bytes of a generated webhook secret: 43 URL-safe characters (WxCC wants 32–256)."""


def new_webhook_secret() -> str:
    """Return a fresh webhook secret (URL-safe, never typed or shown)."""
    return random.token_urlsafe(WEBHOOK_SECRET_BYTES)


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
        return OrganisationConnection(
            ContactCenterKind(str(record["kind"])),
            str(record["api_base_url"]),
            str(record["client_id"]),
            has_client_secret=bool(record.get("client_secret")),
            has_refresh_token=bool(record.get("refresh_token")),
            webhook_secret_at=datetime.fromisoformat(rotated) if isinstance(rotated, str) else None,
        )

    async def save(self, organisation_id: int, change: ConnectionChange, now: datetime) -> None:
        """Store the connection; a ``None`` secret keeps the stored one.

        A new connection gets its webhook secret at once, so its address works from the start.
        """
        record = dict(await self._record(organisation_id) or {})
        record |= {
            "kind": change.kind.value,
            "api_base_url": change.api_base_url.rstrip("/"),
            "client_id": change.client_id,
        }
        if change.client_secret is not None:
            record["client_secret"] = change.client_secret
        if change.refresh_token is not None:
            record["refresh_token"] = change.refresh_token
        if not record.get("webhook_secret"):
            record |= {"webhook_secret": new_webhook_secret(), "webhook_secret_at": now.isoformat()}
        await self._store.put(contact_center_credential_name(organisation_id), record, now)

    async def rotate_webhook_secret(self, organisation_id: int, now: datetime) -> None:
        """Replace the webhook secret of an existing connection; nothing without one."""
        record = await self._record(organisation_id)
        if record is None:
            return
        record |= {"webhook_secret": new_webhook_secret(), "webhook_secret_at": now.isoformat()}
        await self._store.put(contact_center_credential_name(organisation_id), record, now)

    async def delete(self, organisation_id: int) -> None:
        """Remove the organisation's connection and its secrets."""
        await self._store.delete(contact_center_credential_name(organisation_id))

    async def webhook_secret(self, organisation_id: int, kind: ContactCenterKind) -> str | None:
        """Return the webhook secret when the organisation is connected to ``kind``; else ``None``.

        For the webhook route only (ADR 0032 point 10); never shown in the portal.
        """
        record = await self._record(organisation_id)
        if record is None or record.get("kind") != kind.value:
            return None
        secret = record.get("webhook_secret")
        return secret if isinstance(secret, str) and secret else None


def sealed_connections(engine: AsyncEngine, settings: Settings) -> SealedConnections | None:
    """Return the connections, or ``None`` without a settings key (nothing can be sealed)."""
    box = secret_box(settings)
    return SealedConnections(CredentialStore(engine, box)) if box is not None else None
