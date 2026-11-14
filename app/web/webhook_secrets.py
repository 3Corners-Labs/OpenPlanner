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

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from fastapi import Request

from app.adapters.db.secret_sightings import StoredSightings
from app.adapters.webhooks import InboundWebhook
from app.config import Settings
from app.domain.ports import Clock, SecretSightings
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


async def _sighting(
    request: Request, inbound: InboundWebhook, secret: str, now: datetime
) -> Sighting | None:
    """Return when ``secret`` was first seen at this platform's address; ``None`` if unknown.

    Read once per process and secret, then kept in ``app.state``; the store is written when the
    secret is new (or its fingerprint is remade under the current key).
    """
    state = request.app.state
    keys = webhook_fingerprint_keys(state.settings)
    made = fingerprint(keys[0], secret)
    cache: dict[str, Sighting] | None = getattr(state, "webhook_sightings", None)
    if cache is None:
        cache = state.webhook_sightings = {}
    cached = cache.get(inbound.source)
    if cached is not None and cached.fingerprint == made:
        return cached
    store: SecretSightings = StoredSightings(state.engine)
    try:
        stored = await store.get(inbound.source)
        seen = sighted(stored, secret, keys, now)
        if seen != stored:
            await store.put(inbound.source, seen)
    except Exception as exc:  # an overlap that cannot be measured is not granted
        log.warning("webhook.sighting_failed", source=inbound.source, error=type(exc).__name__)
        return None
    cache[inbound.source] = seen
    return seen


async def installation_status(
    request: Request, inbound: InboundWebhook
) -> InstallationSecrets | None:
    """Return the installation-wide secrets of a platform, or ``None`` without a current secret.

    The previous value counts only with a known sighting of the current secret; without one it is
    dropped. The finding (``ended`` after the overlap, ``same`` as the current value) is what
    System status shows.
    """
    settings: Settings = request.app.state.settings
    current = inbound.secret(settings)
    if current is None:
        return None
    clock: Clock = request.app.state.clock
    now = clock.now()
    value = current.get_secret_value()
    previous_secret = inbound.previous(settings)
    previous = previous_secret.get_secret_value() if previous_secret is not None else None
    seen = await _sighting(request, inbound, value, now)
    if seen is None or previous is None:
        return InstallationSecrets(WebhookSecrets(value), None)
    overlap = timedelta(minutes=settings.webhook_secret_overlap_minutes)
    return InstallationSecrets(
        installation_secrets(value, previous, seen.since),
        previous_finding(value, previous, seen.since, now, overlap),
    )


async def installation_secrets_of(
    request: Request, inbound: InboundWebhook
) -> WebhookSecrets | None:
    """Return the secrets the gate verifies a platform's installation-wide address under."""
    status = await installation_status(request, inbound)
    return status.secrets if status is not None else None
