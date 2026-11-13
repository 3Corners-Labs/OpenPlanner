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
event digest itself, under the platform's code. ``LEGACY_READ`` keeps that shape readable for an
event remembered before the upgrade, so a redelivery across it is still a replay. Memory lasts
``WEBHOOK_MEMORY`` (30 days), so release 0.7.0 may drop the reading and ``legacy_key``.
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta

WEBHOOK_MEMORY = timedelta(days=30)
"""How long an event is remembered; older ones are forgotten on the next event. A fixed rule of
the replay protection, not a setting (ADR 0124 point 7)."""

INSTALLATION_SCOPE = "installation"
"""The scope of an address that belongs to no organisation (the installation-wide ones)."""

LEGACY_READ = True
"""Whether a key remembered in the shape before ADR 0124 step 3a still counts. Release 0.7.0 may
set it aside: by then every such key is older than ``WEBHOOK_MEMORY``."""

_SEPARATOR = "\x1f"


@dataclass(frozen=True)
class ReplayContext:
    """What the memory needs to know about a delivery besides the event itself.

    Attributes:
        scope: Whose address the event came in at (``scope_of``).
        body_identity: The identity of the verified body (``body_identity``); used only for an
            event that has no identifier of its own. Empty: there is none to use.
    """

    scope: str = INSTALLATION_SCOPE
    body_identity: str = ""


INSTALLATION_REPLAY = ReplayContext()
"""A delivery at an installation-wide address, with no body identity (a caller that has none)."""


def scope_of(organisation_id: int | None) -> str:
    """Return the memory's scope of an address: the organisation's id, or the installation's."""
    return INSTALLATION_SCOPE if organisation_id is None else f"organisation:{organisation_id}"


def body_identity(body: bytes) -> str:
    """Return the identity of a verified body: its SHA-256, tagged so it never meets an event id."""
    return "body:" + hashlib.sha256(body).hexdigest()


def replay_key(source: str, scope: str, identity: str) -> str:
    """Return the key that is remembered for ``identity`` at ``scope`` of platform ``source``."""
    return hashlib.sha256(_SEPARATOR.join((source, scope, identity)).encode()).hexdigest()


def legacy_key(event_key: str) -> str:
    """Return the key as it was stored before step 3a: the adapter's digest itself.

    Empty when there is nothing to read (no identifier of the event's own, or the old shape is
    no longer read).
    """
    return event_key if LEGACY_READ else ""
