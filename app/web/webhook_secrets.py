"""The secrets of the installation's own webhook addresses, as the gate and System status read them.

An organisation's secrets come from its sealed connection (``ContactCenterConnectionService``).
The installation's own, ``WEBEX_WEBHOOK_SECRET`` and ``WXCC_WEBHOOK_SECRET``, come from the
environment, which the application cannot write (ADR 0124 point 8). Their rotation is read from
the environment too: an optional ``..._PREVIOUS`` value, which verifies for
``webhook_secret_overlap_minutes`` counted from the moment the application first saw the current
secret. That moment is kept in the database next to a keyed fingerprint of the secret
(``app.adapters.db.secret_sightings``), never the secret, so a restart does not start the overlap
again and a changed secret does.

    settings ─► current, previous ─► sighting (stored, or now) ─► ``WebhookSecrets``

The sighting is read once per process and secret and then kept in memory: a secret in the
environment cannot change without a restart. When the database cannot be read the previous value
is *not* used: an overlap that cannot be measured never extends.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from fastapi import FastAPI, Request

from app.adapters.webhooks import InboundWebhook
from app.config import Settings
from app.domain.ports import Clock, UnitOfWork
from app.domain.webhook_secrets import (
    SecretFinding,
    Sighting,
    WebhookSecrets,
    fingerprint,
    installation_secrets,
    previous_finding,
    sighted,
)
from app.keys import webhook_fingerprint_keys

log = structlog.get_logger()


@dataclass(frozen=True)
class InstallationSecrets:
    """What the environment holds for one platform's installation-wide webhook address.

    Attributes:
        secrets: The secrets the gate verifies under.
        finding: What System status says about the previous value, or ``None``.
    """

    secrets: WebhookSecrets
    finding: SecretFinding | None


class InstallationSecretsService:
    """Reads the installation's webhook secrets and keeps their first sightings.

    Built once at start-up (``install_installation_secrets``). A secret in the environment cannot
    change without a restart, so a sighting is read from the database once per process and secret
    and then kept here; it is written when the secret is new, or when its fingerprint is made
    again under the current key.

    Args:
        uow: Opens the transactions that read and write the sightings.
        clock: Current time.
        live: Returns the settings as they are now.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, live: Callable[[], Settings]) -> None:
        """Store the collaborators; no secret has been seen yet."""
        self._uow = uow
        self._clock = clock
        self._live = live
        self._seen: dict[str, Sighting] = {}

    async def _sighting(self, source: str, secret: str, now: datetime) -> Sighting | None:
        """Return when ``secret`` was first seen at ``source``'s address; ``None`` if unknown."""
        keys = webhook_fingerprint_keys(self._live())
        cached = self._seen.get(source)
        if cached is not None and cached.fingerprint == fingerprint(keys[0], secret):
            return cached
        try:
            async with self._uow.locked() as tx:
                stored = await tx.get_secret_sighting(source)
                seen = sighted(stored, secret, keys, now)
                if seen != stored:
                    await tx.save_secret_sighting(source, seen)
        except Exception as exc:  # an overlap that cannot be measured is not granted
            log.warning("webhook.sighting_failed", source=source, error=type(exc).__name__)
            return None
        self._seen[source] = seen
        return seen

    async def status(self, inbound: InboundWebhook) -> InstallationSecrets | None:
        """Return the installation-wide secrets of a platform, or ``None`` without a current one.

        The previous value counts only with a known sighting of the current secret; without one it
        is dropped. The finding (``ended`` after the overlap, ``same`` as the current value) is
        what System status shows.
        """
        settings = self._live()
        current = inbound.secret(settings)
        if current is None:
            return None
        now = self._clock.now()
        value = current.get_secret_value()
        previous_secret = inbound.previous(settings)
        previous = previous_secret.get_secret_value() if previous_secret is not None else None
        seen = await self._sighting(inbound.source, value, now)
        if seen is None or previous is None:
            return InstallationSecrets(WebhookSecrets(value), None)
        overlap = timedelta(minutes=settings.webhook_secret_overlap_minutes)
        return InstallationSecrets(
            installation_secrets(value, previous, seen.since),
            previous_finding(value, previous, seen.since, now, overlap),
        )


def install_installation_secrets(
    app: FastAPI, uow: UnitOfWork, clock: Clock, live: Callable[[], Settings]
) -> None:
    """Put the installation's secrets service on ``app.state`` (called by an install function)."""
    app.state.installation_secrets = InstallationSecretsService(uow, clock, live)


async def installation_status(
    request: Request, inbound: InboundWebhook
) -> InstallationSecrets | None:
    """Return the installation-wide secrets of a platform for this application."""
    service: InstallationSecretsService = request.app.state.installation_secrets
    return await service.status(inbound)


async def installation_secrets_of(
    request: Request, inbound: InboundWebhook
) -> WebhookSecrets | None:
    """Return the secrets the gate verifies a platform's installation-wide address under."""
    status = await installation_status(request, inbound)
    return status.secrets if status is not None else None
