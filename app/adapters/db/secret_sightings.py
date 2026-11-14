"""SQL queries of the first sighting of each installation webhook secret (ADR 0124 point 8).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction. One row per
platform (``webhook_secret_sightings``): the platform's code, a keyed fingerprint of the secret
(``app.domain.webhook_secrets.fingerprint``, an HMAC that cannot be turned back into the secret)
and when the application first saw it. The installation's own webhook secrets come from the
environment, which the application cannot write, so the moment of their rotation is the moment
they were first seen. No secret and no previous secret is stored.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import WebhookSecretSightingRow
from app.domain.webhook_secrets import Sighting


class SecretSightingQueries:
    """The sightings' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def get_secret_sighting(self, platform: str) -> Sighting | None:
        """Return the stored sighting of ``platform`` (``webex``, ``wxcc``), or ``None``."""
        row = await self._session.get(WebhookSecretSightingRow, platform)
        return Sighting(row.fingerprint, row.first_seen) if row else None

    async def save_secret_sighting(self, platform: str, sighting: Sighting) -> None:
        """Store the sighting of ``platform``, replacing the one before."""
        await self._session.merge(
            WebhookSecretSightingRow(
                platform=platform, fingerprint=sighting.fingerprint, first_seen=sighting.since
            )
        )
        await self._session.flush()
