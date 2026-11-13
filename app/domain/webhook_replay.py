"""How an inbound webhook event is remembered, so a replay is recorded once (ADR 0124 point 7).

An event is identified by what the platform gave it (``event_key``: a digest of ids and types the
platform's adapter makes) or, when it gave it nothing of its own, by the SHA-256 of its verified
body: the same signed body twice is a replay by definition. What is remembered is a digest of the
platform's code, the organisation the address belongs to and that identity:

    remembered key = sha256(platform ␟ scope ␟ identity)

so the same event id delivered to two organisations is two events, and delivered twice to one is
a replay. The memory holds digests, never a body, an id in clear or a secret; the key does not
depend on which secret verified the call, and it is asked for only after the signature held.

The memory's table is the one of ADR 0010 (``webhook_events``: source and key, 64 characters);
only the key's derivation changed, so no migration was needed (ADR 0116: a persisted name is
renamed by writing the new one and reading both). Before this release the key was the adapter's
event digest itself, under the platform's code. ``IntegrationService`` still reads that shape
(``webhook_event_seen``), so an event remembered before the upgrade is a replay wherever it comes
back. Memory lasts ``WEBHOOK_MEMORY`` (30 days), so release 0.7.0 removes that reading together
with the port method.
"""

import hashlib
from datetime import timedelta

WEBHOOK_MEMORY = timedelta(days=30)
"""How long an event is remembered; older ones are forgotten on the next event. A fixed rule of
the replay protection, not a setting (ADR 0124 point 7)."""

INSTALLATION_SCOPE = "installation"
"""The scope of an address that belongs to no organisation (the installation-wide ones)."""

_SEPARATOR = "\x1f"


def scope_of(organisation_id: int | None) -> str:
    """Return the memory's scope of an address: the organisation's id, or the installation's."""
    return INSTALLATION_SCOPE if organisation_id is None else f"organisation:{organisation_id}"


def body_identity(body: bytes) -> str:
    """Return the identity of a verified body: its SHA-256, tagged so it never meets an event id."""
    return "body:" + hashlib.sha256(body).hexdigest()


def replay_key(source: str, scope: str, identity: str) -> str:
    """Return the key that is remembered for ``identity`` at ``scope`` of platform ``source``."""
    return hashlib.sha256(_SEPARATOR.join((source, scope, identity)).encode()).hexdigest()
