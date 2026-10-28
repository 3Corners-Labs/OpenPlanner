"""Security events: rejected webhooks and failing credentials, deduplicated (ADR 0034, AU-4).

A webhook with a bad signature or a refused Webex token refresh says something about the
platform's security, not about one group: it is a global event of category ``security``, so
only the superuser sees it in the portal, and it is shipped to the log platform. A flood (a
scanner hammering the webhook URL) must not fill the table, so per kind of event at most one
is written per window; the next one after the window says how many were held back:

    first rejection ─► event (detail ``source:webex``)
    ...999 more within the window ─► counted only
    first one after the window ─► event (detail ``source:webex,suppressed:999``)

The counters live in memory per process (web and worker each keep their own), so a restart
loses at most one window's count; the container log still has every single line.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Category, Outcome, Source
from app.domain.ports import Clock, UnitOfWork

WEBHOOK_REJECTED = "webhook.rejected"
"""A webhook whose signature or timestamp did not verify."""

TOKEN_REFRESH_FAILED = "webex.token_refresh"  # noqa: S105  # nosec B105 - an event key
"""Webex refused, or could not be reached for, a token refresh."""

INVALID_SIGNATURE = "invalid_signature"
"""Reason of a rejected webhook."""


@dataclass
class _Window:
    """When the current window of one kind of event started and how many it held back."""

    started: datetime
    suppressed: int = 0


class SecurityLog:
    """Writes global security events, at most one per kind per window.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        window: How long further events of the same kind are only counted.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, window: timedelta) -> None:
        """Store the collaborators; no window is open yet."""
        self._uow = uow
        self._clock = clock
        self._window = window
        self._windows: dict[tuple[str, str, str], _Window] = {}

    async def record(
        self, action: str, outcome: Outcome, *, reason: str = "", detail: str = ""
    ) -> bool:
        """Write the event unless one of the same kind was written within the window.

        Args:
            action: Event key, e.g. ``webhook.rejected``.
            outcome: ``denied`` for a refused caller, ``failure`` for our own failing call.
            reason: Machine-readable reason, e.g. ``invalid_signature``.
            detail: Machine keys, e.g. ``source:webex``; never values from the request.

        Returns:
            Whether an event was written (``False``: only counted).
        """
        now = self._clock.now()
        key = (action, reason, detail)
        window = self._windows.get(key)
        if window is not None and now - window.started < self._window:
            window.suppressed += 1
            return False
        held_back = window.suppressed if window is not None else 0
        self._windows[key] = _Window(now)
        keys = [detail] if detail else []
        if held_back:
            keys.append(f"suppressed:{held_back}")
        async with self._uow.locked() as tx:
            await tx.add_audit_event(
                AuditEvent(
                    now,
                    SYSTEM_ACTOR,
                    action,
                    outcome,
                    reason,
                    detail=",".join(keys),
                    source=Source.SYSTEM,
                    category=Category.SECURITY,
                )
            )
        return True
