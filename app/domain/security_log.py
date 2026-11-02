"""Security events: attempts on public entry points and failing credentials (ADR 0034, 0053).

A webhook with a bad signature, a rate-limit flood, a bot filling in the honeypot, a
cross-site POST, a failed admin login or a guessed token says something about the platform's
security, not about one group: it is a global event of category ``security``, so only the
superuser sees it in the portal, and it is shipped to the log platform. Each one names its
caller by pseudonym (``client:<pseudonym>``, ``app.domain.client_pseudonym``), never by
address. A flood must not fill the table, so per kind of event and caller at most one is
written per window; the next one after the window says how many were held back:

    first rejection ─► event (detail ``source:webex,client:3f2a…``)
    ...999 more within the window ─► counted only
    first one after the window ─► event (detail ``source:webex,client:3f2a…,suppressed:999``)

At most ``max_sources`` windows are kept; beyond that, expired ones are dropped first and
further callers count together under client ``many``, so a distributed attack costs bounded
memory and rows. The counters live in memory per process (web and worker each keep their
own), so a restart loses at most one window's count; the container log still has every line.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Category, Outcome, Source
from app.domain.client_pseudonym import MANY_CLIENTS
from app.domain.ports import Clock, UnitOfWork
from app.domain.tunable import value_of

WEBHOOK_REJECTED = "webhook.rejected"
"""A webhook whose signature or timestamp did not verify."""

TOKEN_REFRESH_FAILED = "webex.token_refresh"  # noqa: S105  # nosec B105 - an event key
"""Webex refused, or could not be reached for, a token refresh."""

DATABASE_ERROR = "database.error"
"""A request failed on a database error that is not a busy lock (ASVS V16.3.4): our own failure."""

DATABASE_FAILED = "database_error"
"""Reason of ``database.error``; the detail names the surface (``surface:web``, …)."""

INVALID_SIGNATURE = "invalid_signature"
"""Reason of a rejected webhook."""

RATE_LIMITED = "request.rate_limited"
"""A caller went over a rate limit (ADR 0053)."""

TOO_MANY_REQUESTS = "too_many_requests"
"""Reason of ``request.rate_limited``."""

REQUEST_REJECTED = "request.rejected"
"""A request refused before it reached the domain (ADR 0053)."""

HONEYPOT = "honeypot"
"""Reason of ``request.rejected``: the hidden form field was filled in (a bot)."""

CROSS_SITE = "cross_site"
"""Reason of ``request.rejected``: the browser marked the POST as cross-site."""

ORIGIN_MISMATCH = "origin_mismatch"
"""Reason of ``request.rejected``: without Fetch Metadata, ``Origin`` was another site."""

TOKEN_REJECTED = "token.rejected"  # noqa: S105  # nosec B105 - an event key
"""A token or link that matches nothing: guessed, forged or long expired (ADR 0053)."""

UNKNOWN_TOKEN = "unknown_token"  # noqa: S105  # nosec B105 - a reason code
"""Reason of ``token.rejected``: an unknown bearer token on the machine API."""

UNKNOWN_MANAGEMENT_LINK = "unknown_management_link"
"""Reason of ``token.rejected``: a ``/b/{token}`` link that matches no booking."""

LINK_REQUIRED = "link_required"
"""Reason of ``token.rejected``: a ``/b/m/{ref}`` page without a link session that holds it."""


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
        max_sources: How many windows (kinds × callers) are kept at most.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        window: timedelta | Callable[[], timedelta],
        *,
        max_sources: int | Callable[[], int] = 1000,
    ) -> None:
        """Store the collaborators; no window is open yet."""
        self._uow = uow
        self._clock = clock
        self._window = window
        self._max_sources = max_sources
        self._windows: dict[tuple[str, str, str, str], _Window] = {}

    async def record(
        self,
        action: str,
        outcome: Outcome,
        *,
        reason: str = "",
        detail: str = "",
        client: str = "",
        actor: str = SYSTEM_ACTOR,
    ) -> bool:
        """Write the event unless one of the same kind was written within the window.

        Args:
            action: Event key, e.g. ``webhook.rejected``.
            outcome: ``denied`` for a refused caller, ``failure`` for our own failing call.
            reason: Machine-readable reason, e.g. ``invalid_signature``.
            detail: Machine keys, e.g. ``source:webex``; never values from the request.
            client: Pseudonym of the caller (``ClientPseudonyms``); empty for our own calls.
            actor: Who acted: ``system``, or ``anonymous`` for a visitor trying to log in.

        Returns:
            Whether an event was written (``False``: only counted).
        """
        now = self._clock.now()
        key = (action, reason, detail, client)
        if key not in self._windows and len(self._windows) >= value_of(self._max_sources):
            self._drop_expired(now)
            if len(self._windows) >= value_of(self._max_sources):
                key = (action, reason, detail, MANY_CLIENTS)
        window = self._windows.get(key)
        if window is not None and now - window.started < value_of(self._window):
            window.suppressed += 1
            return False
        held_back = window.suppressed if window is not None else 0
        self._windows[key] = _Window(now)
        keys = [detail] if detail else []
        if key[3]:
            keys.append(f"client:{key[3]}")
        if held_back:
            keys.append(f"suppressed:{held_back}")
        async with self._uow.locked() as tx:
            await tx.add_audit_event(
                AuditEvent(
                    now,
                    actor,
                    action,
                    outcome,
                    reason,
                    detail=",".join(keys),
                    source=Source.SYSTEM,
                    category=Category.SECURITY,
                )
            )
        return True

    def _drop_expired(self, now: datetime) -> None:
        """Forget windows that ended; their held-back counts are lost (logged per line)."""
        for key in [
            k for k, w in self._windows.items() if now - w.started >= value_of(self._window)
        ]:
            del self._windows[key]
