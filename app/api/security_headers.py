"""ASGI middleware that adds deny-by-default security headers to every HTTP response."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURITY_HEADERS: dict[bytes, bytes] = {
    b"content-security-policy": b"default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    b"strict-transport-security": b"max-age=63072000; includeSubDomains",
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    # same-origin, not no-referrer: with no-referrer browsers send ``Origin: null`` on form
    # posts, which breaks the origin fallback in ``app.web.protection``. Other sites still
    # receive no referrer, so management tokens in paths never leak.
    b"referrer-policy": b"same-origin",
    # Pages need no powerful browser features; the video call itself runs in Webex.
    b"permissions-policy": (
        b"camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        b"display-capture=(), interest-cohort=()"
    ),
    # Process isolation: no cross-origin windows or resources can attach to our pages. The
    # embeddable widget (later) serves its script with its own Cross-Origin-Resource-Policy.
    b"cross-origin-opener-policy": b"same-origin",
    b"cross-origin-embedder-policy": b"require-corp",
    b"cross-origin-resource-policy": b"same-origin",
}
"""Strictest defaults. A route that renders HTML sets its own CSP, which is then kept."""


class SecurityHeadersMiddleware:
    """Add ``SECURITY_HEADERS`` to responses, without overriding headers a route already set."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass non-HTTP scopes through; inject headers into ``http.response.start``."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in SECURITY_HEADERS.items() if k not in present]
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        await self.app(scope, receive, send_with_headers)
