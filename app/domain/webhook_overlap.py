"""Checking a webhook delivery under the current secret and the previous one (ADR 0124 point 8).

The rule of ``app.domain.webhook_secrets`` applied to one delivery: both secrets are always
compared, the time of the check alone decides whether the previous one counts, and a delivery that
the previous secret verified is accepted exactly like one the current secret verified; the verdict
says which matched so that a count for the connection card can tell them apart, and nothing else
may.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.inbound_gate import Refusal
from app.domain.webhook_secrets import WebhookSecrets


class Match(StrEnum):
    """Which secret a delivery verified under."""

    CURRENT = "current"
    PREVIOUS = "previous"


@dataclass(frozen=True)
class Verdict:
    """What checking a delivery against both secrets came to.

    Attributes:
        refusal: Why it is refused; ``None`` when a secret verified it.
        by: Which secret verified it; ``None`` when refused.
    """

    refusal: Refusal | None
    by: Match | None = None


def verify_with_overlap(
    secrets: WebhookSecrets,
    now: datetime,
    overlap: timedelta,
    check: Callable[[str], Refusal | None],
) -> Verdict:
    """Check a delivery under the current secret and the previous one, always both.

    Args:
        secrets: The secrets of the address.
        now: The time of the check; it alone decides whether the previous secret counts.
        overlap: How long a previous secret verifies (``webhook_secret_overlap_minutes``).
        check: The platform's check of one secret: ``None`` when authentic and recent, else
            ``Refusal.SIGNATURE`` or ``Refusal.STALE``.

    Returns:
        The current secret's verdict when it verifies, else the previous secret's when it counts
        and verifies, else a refusal: ``stale`` when a secret that counts verified the signature
        but not the time, otherwise ``invalid_signature``. A previous secret that no longer counts
        is compared all the same and contributes nothing, not even a ``stale``.
    """
    counts = secrets.previous_counts(now, overlap)
    on_current = check(secrets.current)
    # The stand-in keeps the work the same when there is no previous secret or it ended.
    on_previous = check(secrets.previous if secrets.previous is not None else secrets.current)
    if on_current is None:
        return Verdict(None, Match.CURRENT)
    if counts and on_previous is None:
        return Verdict(None, Match.PREVIOUS)
    stale = on_current is Refusal.STALE or (counts and on_previous is Refusal.STALE)
    return Verdict(Refusal.STALE if stale else Refusal.SIGNATURE)
