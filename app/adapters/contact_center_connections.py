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
``webhook_secret`` and ``webhook_secrets`` are for the webhook route and the dispatcher only,
outside the domain's views.

A rotated webhook secret leaves the one before it in the same sealed record
(``previous_webhook_secret``, ADR 0124 point 8), sealed like the current one; the time of the
rotation is ``webhook_secret_at``. Whether it still verifies is decided by the clock at each call
(``app.domain.webhook_secrets``); here it is only *removed*: at any write of the record once no
setting could keep it (``lapsed``), and by ``drop_expired_previous`` for the periodic sweep, which
knows the live overlap.
"""

import secrets as random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.adapters.db.credentials import Secrets
from app.adapters.db.record_names import contact_center_credential_name, organisation_tokens_name
from app.domain.contact_center import (
    ConnectionChange,
    ContactCenterKind,
    OrganisationConnection,
    Registration,
)
from app.domain.networks import NetworkList
from app.domain.source_networks import read_stored_networks
from app.domain.webhook_secrets import OVERLAP_MAX, WebhookSecrets, rotated, settled

WEBHOOK_SECRET_BYTES = 32
"""Random bytes of a generated webhook secret: 43 URL-safe characters (WxCC wants 32–256)."""


PREVIOUS_KEY = "previous_webhook_secret"
"""The field of the sealed record that holds the secret before the last rotation."""


def new_webhook_secret() -> str:
    """Return a fresh webhook secret (URL-safe, never typed or shown)."""
    return random.token_urlsafe(WEBHOOK_SECRET_BYTES)


def _registration_of(value: object) -> Registration | None:
    """Return a stored registration state, or ``None`` for none or one that does not parse."""
    try:
        return Registration(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _time_of(value: object) -> datetime | None:
    """Return a stored ISO time, or ``None`` for anything else."""
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def secrets_of(record: dict[str, object]) -> WebhookSecrets | None:
    """Return the webhook secrets a sealed record holds, or ``None`` without a current one."""
    current, previous = record.get("webhook_secret"), record.get(PREVIOUS_KEY)
    if not isinstance(current, str) or not current:
        return None
    return WebhookSecrets(
        current,
        previous if isinstance(previous, str) and previous else None,
        _time_of(record.get("webhook_secret_at")),
    )


def _write_secrets(record: dict[str, object], secrets: WebhookSecrets) -> None:
    """Put ``secrets`` into ``record``; a missing previous secret leaves no field behind."""
    record["webhook_secret"] = secrets.current
    record["webhook_secret_at"] = secrets.rotated_at.isoformat() if secrets.rotated_at else None
    if secrets.previous is None:
        record.pop(PREVIOUS_KEY, None)
    else:
        record[PREVIOUS_KEY] = secrets.previous


def _tidied(record: dict[str, object], now: datetime) -> dict[str, object]:
    """Drop a previous secret that no setting could keep alive any more, at a write."""
    held = secrets_of(record)
    if held is not None and held.previous is not None:
        if settled(held, now, OVERLAP_MAX).previous is None:
            record.pop(PREVIOUS_KEY, None)
    return record


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
            has_previous_webhook_secret=bool(record.get(PREVIOUS_KEY)),
            allowed_networks=networks,
            inbound_enabled=record.get("inbound_enabled") is not False,
            registration=_registration_of(record.get("registration")),
        )

    async def paused(self, organisation_ids: Sequence[int]) -> dict[int, ContactCenterKind]:
        """Return the organisations among ``organisation_ids`` whose address is paused.

        One query for all their records; only a stored ``False`` pauses, as in ``get``.
        """
        names = {contact_center_credential_name(i): i for i in organisation_ids}
        records = await self._store.get_many(list(names))
        return {
            names[name]: ContactCenterKind(str(record["kind"]))
            for name, record in records.items()
            if record.get("inbound_enabled") is False
        }

    async def save(self, organisation_id: int, change: ConnectionChange, now: datetime) -> None:
        """Store the connection; a ``None`` secret keeps the stored one.

        A new connection gets its webhook secret at once, so its address works from the start;
        a changed API host or client id gets a new one, so a subscription the previous tenant
        still holds no longer verifies (CC-4d). Changing the allowed networks alone touches
        neither the secret nor anything else of the record.
        """
        record = _tidied(dict(await self._record(organisation_id) or {}), now)
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
            # Another tenant (host or client): the old tenant's subscription must stop verifying,
            # so nothing is kept as a previous secret (an overlap is for a rotation, not a move).
            record |= {"webhook_secret": new_webhook_secret(), "webhook_secret_at": now.isoformat()}
            record.pop(PREVIOUS_KEY, None)
        await self._store.put(contact_center_credential_name(organisation_id), record, now)

    async def set_inbound_enabled(self, organisation_id: int, enabled: bool, now: datetime) -> None:
        """Pause or resume the address (ADR 0124 point 9): that one field, nothing else.

        Nothing happens for an organisation whose record went away in the meantime, as for
        ``rotate_webhook_secret``.
        """
        record = await self._record(organisation_id)
        if record is None:
            return
        _tidied(record, now)
        record["inbound_enabled"] = enabled
        await self._store.put(contact_center_credential_name(organisation_id), record, now)

    async def set_registration(
        self, organisation_id: int, state: Registration | None, now: datetime
    ) -> None:
        """Remember what the last registration came to; ``None`` forgets it (ADR 0124 point 8).

        Only that field changes. Nothing happens for an organisation whose record went away.
        """
        record = await self._record(organisation_id)
        if record is None:
            return
        _tidied(record, now)
        if state is None:
            record.pop("registration", None)
        else:
            record["registration"] = state.value
        await self._store.put(contact_center_credential_name(organisation_id), record, now)

    async def rotate_webhook_secret(self, organisation_id: int, now: datetime) -> None:
        """Replace the webhook secret of an existing connection; nothing without one.

        The secret that was current is kept, sealed, as the previous one (ADR 0124 point 8); the
        previous one from before it is gone, whatever was left of its overlap.
        """
        record = await self._record(organisation_id)
        if record is None:
            return
        _write_secrets(record, rotated(secrets_of(record), new_webhook_secret(), now))
        await self._store.put(contact_center_credential_name(organisation_id), record, now)

    async def drop_expired_previous(
        self, organisation_ids: Sequence[int], now: datetime, overlap: timedelta
    ) -> int:
        """Remove the previous secrets whose overlap has ended at ``now``; return how many.

        For the periodic sweep, which knows the live overlap. One read for all the records, one
        write per record that changes.
        """
        names = {contact_center_credential_name(i): i for i in organisation_ids}
        removed = 0
        for name, record in (await self._store.get_many(list(names))).items():
            held = secrets_of(record)
            if held is None or held.previous is None:
                continue
            if settled(held, now, overlap).previous is not None:
                continue
            record.pop(PREVIOUS_KEY, None)
            await self._store.put(name, record, now)
            removed += 1
        return removed

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

    async def webhook_secrets(
        self, organisation_id: int, kind: ContactCenterKind
    ) -> WebhookSecrets | None:
        """Return the current and the previous webhook secret with the time of the rotation.

        ``None`` unless the organisation is connected to ``kind`` with a secret. For the webhook
        gate only (ADR 0124 point 8): whether the previous one still counts is decided there.
        """
        record = await self._record(organisation_id)
        if record is None or record.get("kind") != kind.value:
            return None
        return secrets_of(record)
