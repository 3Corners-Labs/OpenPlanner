"""Security events from HTTP requests: who called, as a pseudonym (ADR 0053).

The one place where a request becomes a security event. Handlers and routes call ``report``
with an event key and a reason; it turns the caller's address into a pseudonym and hands both
to the application's ``SecurityLog``, which deduplicates per kind and caller. Nothing from
the request besides the address is used, and the address itself is never stored.

    429 handler, honeypot, same-origin check, login callback, token checks
        ─► report(request, action, reason) ─► SecurityLog.record(..., client=pseudonym)

A failure here is logged and swallowed: the caller still gets the answer it would have got.
"""

import structlog
from fastapi import Request

from app.domain.audit import SYSTEM_ACTOR, Outcome
from app.domain.client_pseudonym import ClientPseudonyms
from app.domain.security_log import SecurityLog

log = structlog.get_logger()


async def report(
    request: Request, action: str, reason: str, *, detail: str = "", actor: str = SYSTEM_ACTOR
) -> None:
    """Record a refused request as a security event with the caller's pseudonym.

    Args:
        request: The refused request; only its client address is read.
        action: Event key, e.g. ``request.rate_limited``.
        reason: Machine-readable reason, e.g. ``too_many_requests``.
        detail: Machine keys, e.g. ``surface:web``; never values from the request.
        actor: ``system``, or ``anonymous`` for a visitor trying to log in.
    """
    security: SecurityLog | None = getattr(request.app.state, "security_log", None)
    pseudonyms: ClientPseudonyms | None = getattr(request.app.state, "client_pseudonyms", None)
    if security is None or pseudonyms is None:  # before startup, or an app without them
        return
    client = pseudonyms.of(request.client.host if request.client else None)
    try:
        await security.record(
            action, Outcome.DENIED, reason=reason, detail=detail, client=client, actor=actor
        )
    except Exception as exc:  # the refusal itself must not turn into a 500
        log.warning("security_log.failed", error=type(exc).__name__)
