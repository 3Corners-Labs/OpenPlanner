"""Middleware that notices forwarded headers from a peer the app does not trust (ADR 0086).

uvicorn rewrites the client address and the scheme from ``X-Forwarded-For`` and
``X-Forwarded-Proto`` only when the peer is in ``FORWARDED_ALLOW_IPS``. This middleware runs
inside uvicorn's, so it sees the result: headers that are there while the app kept the peer's
address or scheme were ignored, and the request is counted in ``ProxyHeaderWatch``. The first
one in every repeat period also becomes a system event ``public_address`` with the reason
``proxy_headers_untrusted`` and the class of the peer (loopback, private, public), never its
address. It changes nothing about the request and never fails it.
"""

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

from app.domain.audit import Outcome
from app.domain.proxy_watch import EVENT_REASON, ProxyHeaderWatch
from app.domain.public_address import EVENT, Forwarded, classify_peer, forwarded_state
from app.domain.system_log import SystemLog

log = structlog.get_logger()


class ProxyHeaderMiddleware:
    """Counts requests whose forwarded headers were ignored; see the module docstring.

    Args:
        app: The wrapped ASGI application.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Store the wrapped application."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Observe the request, then pass it on unchanged."""
        if scope["type"] == "http":
            await _observe(scope)
        await self._app(scope, receive, send)


async def _observe(scope: Scope) -> None:
    """Count the request when forwarded headers came and were ignored; never raises."""
    state = getattr(scope.get("app"), "state", None)
    watch: ProxyHeaderWatch | None = getattr(state, "proxy_watch", None)
    if watch is None:
        return
    headers = {name: value for name, value in scope["headers"] if name.startswith(b"x-forwarded-")}
    if not headers:
        return
    client = scope.get("client")
    peer = client[0] if client else None
    seen = forwarded_state(
        forwarded_for=_text(headers.get(b"x-forwarded-for")),
        forwarded_proto=_text(headers.get(b"x-forwarded-proto")),
        client=peer,
        scheme=scope.get("scheme", "http"),
    )
    if seen is not Forwarded.IGNORED or not watch.observe(peer):
        return
    system_log: SystemLog | None = getattr(state, "system_log", None)
    if system_log is None:
        return
    try:
        await system_log.record(
            EVENT,
            Outcome.FAILURE,
            reason=EVENT_REASON,
            detail=f"peer:{classify_peer(peer).value}",
        )
    except Exception as exc:  # an event that cannot be written never fails the request
        log.warning("address.proxy_event_failed", error=type(exc).__name__)


def _text(raw: bytes | None) -> str | None:
    """Decode a header value (latin-1 decodes every byte)."""
    return raw.decode("latin-1") if raw is not None else None
