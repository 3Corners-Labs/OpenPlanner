"""The secrets a webhook address verifies under, and how one replaces another (ADR 0124 point 8).

A platform signs each delivery with the secret of its subscription. When that secret is replaced,
the platform keeps signing with the old one until it has taken up the new one, so refusing the old
one at once loses events. The rule here is the overlap: after a rotation the *previous* secret
keeps verifying for ``webhook_secret_overlap_minutes`` (class C, 0 to 1440, 0 means at once).

    rotate(secrets, new, now) ─► WebhookSecrets(current=new, previous=what was current)
    verify_with_overlap(secrets, now, overlap, check) ─► Verdict(refusal | by current | by previous)
                                          (in ``app.domain.webhook_overlap``)

Three rules carry the design:

- **The clock decides, at the moment of checking.** ``WebhookSecrets.previous_counts`` compares the
  time of the check with the time of the rotation plus the overlap as it is *now*, so no job has
  to remove anything for the previous secret to stop counting, and a shorter overlap in the portal
  (0 for a secret that leaked) takes effect for the next call. Removing the sealed value is
  housekeeping (``lapsed`` for the write path, ``settled`` for the sweep), never the rule.
- **Both secrets are always compared**, the previous one too when it no longer counts (against a
  stand-in, its result thrown away), and neither comparison stops the other, so the time a check
  takes says nothing about which secret matched or whether a previous secret exists. Each
  comparison is the platform's own constant-time one (``hmac.compare_digest``).
- **A delivery verified by the previous secret is accepted like any other**: the verdict says which
  secret matched (``Match``) so the card can count it, and nothing else differs.

**A second rotation inside the overlap** (the ADR does not say; decided here): the secret that was
current becomes the previous one, always, and the one from before it stops at once, however much
of its overlap was left. There is never more than one previous secret, so the card's sentence ("the
previous secret still verifies until ...") is one sentence, and a secret that leaked and was
rotated away is not kept alive by rotating again to be safe.

The installation's own secrets come from the environment, which the application cannot write.
Their rotation moment is therefore the moment the application first saw the current secret, kept
as a fingerprint (``fingerprint``: an HMAC under a key the application holds, so it cannot be
turned back into the secret, even for a short secret), and an optional previous value from the
environment verifies for the overlap counted from that moment (``sighted``,
``installation_secrets``). ``previous_finding`` says what System status warns about.
"""

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

OVERLAP_MAX = timedelta(minutes=1440)
"""The longest overlap the setting allows (``webhook_secret_overlap_minutes`` bounds 0 to 1440).
A sealed previous secret older than this can never count again, whatever the setting is."""


@dataclass(frozen=True)
class WebhookSecrets:
    """The secrets one address verifies under, with the moment the current one began.

    Attributes:
        current: The secret now in force.
        previous: The secret before it, or ``None``.
        rotated_at: When ``current`` began; the overlap of ``previous`` is counted from it.
            ``None`` means unknown, and then ``previous`` never counts.
    """

    current: str = field(repr=False)
    previous: str | None = field(default=None, repr=False)
    rotated_at: datetime | None = None

    def previous_ends(self, overlap: timedelta) -> datetime | None:
        """Return when the previous secret stops verifying, or ``None`` without one."""
        if self.previous is None or self.rotated_at is None:
            return None
        return self.rotated_at + overlap

    def previous_counts(self, now: datetime, overlap: timedelta) -> bool:
        """Return whether the previous secret verifies at ``now``: decided by the clock, here."""
        ends = self.previous_ends(overlap)
        return ends is not None and now < ends


def rotated(secrets: WebhookSecrets | None, new: str, now: datetime) -> WebhookSecrets:
    """Return the secrets after ``new`` replaces the current one at ``now``.

    The secret that was current becomes the previous one and the one before it is gone (see the
    module text for a second rotation inside the overlap). Without secrets yet there is no
    previous.
    """
    return WebhookSecrets(new, secrets.current if secrets else None, now)


def previous_until(
    has_previous: bool, rotated_at: datetime | None, now: datetime, overlap: timedelta
) -> datetime | None:
    """Return when a previous secret stops verifying, or ``None`` when none verifies at ``now``.

    For the connection card, which sees whether a previous secret is kept (never the secret) and
    when the rotation was: the same rule as ``WebhookSecrets.previous_counts``.
    """
    if not has_previous or rotated_at is None or now >= rotated_at + overlap:
        return None
    return rotated_at + overlap


def lapsed(secrets: WebhookSecrets, now: datetime) -> bool:
    """Return whether the previous secret can never count again, so it may be removed at a write.

    The longest overlap the setting allows has passed; the write path of the sealed record knows
    no setting, and this holds for every setting.
    """
    ends = secrets.previous_ends(OVERLAP_MAX)
    return secrets.previous is not None and (ends is None or now >= ends)


def settled(secrets: WebhookSecrets, now: datetime, overlap: timedelta) -> WebhookSecrets:
    """Return ``secrets`` without a previous secret whose overlap has ended at ``now``."""
    if secrets.previous is None or secrets.previous_counts(now, overlap):
        return secrets
    return WebhookSecrets(secrets.current, None, secrets.rotated_at)


# --- the installation's own secrets ------------------------------------------------------------


def fingerprint(key: bytes, secret: str) -> str:
    """Return the HMAC-SHA256 of ``secret`` under ``key``, in hex: no way back to the secret.

    The key is one the application holds and the database does not, so a stolen database says
    nothing about a short or typed secret; never a plain hash of it.
    """
    return hmac.new(key, secret.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Sighting:
    """The moment the application first saw a secret, and its fingerprint (never the secret).

    Attributes:
        fingerprint: ``fingerprint`` of the secret under the current key.
        since: When the application first saw it.
    """

    fingerprint: str
    since: datetime


def sighted(stored: Sighting | None, secret: str, keys: Sequence[bytes], now: datetime) -> Sighting:
    """Return the sighting of ``secret``: the stored one when it is the same secret, else ``now``.

    Args:
        stored: What the database holds for this platform, or ``None``.
        secret: The secret in the environment now.
        keys: The keys the fingerprint may have been made under, the current one first. During a
            rotation of the management key the stored fingerprint is still under the previous
            one: it matches there and is re-made under the current key with its time kept, so a
            key rotation never starts the overlap again.
        now: The time of this reading.
    """
    made = [fingerprint(key, secret) for key in keys]
    if stored is not None:
        if hmac.compare_digest(stored.fingerprint, made[0]):
            return stored
        if any(hmac.compare_digest(stored.fingerprint, other) for other in made[1:]):
            return Sighting(made[0], stored.since)
    return Sighting(made[0], now)


def installation_secrets(current: str, previous: str | None, since: datetime) -> WebhookSecrets:
    """Return the secrets of an installation-wide address.

    ``since`` is when ``current`` was first seen.

    A previous value equal to the current one is dropped: it adds nothing and is a finding.
    """
    same = previous is not None and hmac.compare_digest(previous.encode(), current.encode())
    return WebhookSecrets(current, None if same else previous, since)


class SecretFinding(StrEnum):
    """What System status says about a previous secret in the environment."""

    ENDED = "ended"
    """It is still set after its overlap ended: remove it from the environment."""
    SAME = "same"
    """It equals the current secret: it does nothing and should go."""


def previous_finding(
    current: str, previous: str | None, since: datetime, now: datetime, overlap: timedelta
) -> SecretFinding | None:
    """Return what is wrong with the previous value of an installation's secret, or ``None``."""
    if previous is None:
        return None
    if hmac.compare_digest(previous.encode(), current.encode()):
        return SecretFinding.SAME
    if now >= since + overlap:
        return SecretFinding.ENDED
    return None
