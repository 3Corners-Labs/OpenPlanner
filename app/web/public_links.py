"""Redirects that name the host stay on the public address (ADR 0086 point 4).

Every link OpenPlanner builds is made on ``PUBLIC_BASE_URL``. One thing builds a link on its
own: Starlette's redirect from ``/admin`` to ``/admin/`` (and back) names the scheme and host of
the *request*, which is the ``Host`` header the caller sent and the scheme uvicorn saw. Behind a
proxy that is not trusted (``FORWARDED_ALLOW_IPS`` wrong) that is ``http://`` and the proxy's
own name, and a forged ``Host`` header would be reflected into a ``Location``. This middleware
rewrites a ``Location`` that starts with the request's own origin to the public address, the one
place where that is done, so no module needs to read the request's address.
"""

from starlette.datastructures import URL, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.domain.public_address import url_on

_REDIRECTS = frozenset({301, 302, 303, 307, 308})


class PublicRedirectMiddleware:
    """Rewrites a redirect to the request's own origin into one on ``PUBLIC_BASE_URL``.

    Args:
        app: The wrapped ASGI application.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Store the wrapped application."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Wrap ``send`` for HTTP requests; pass everything else on."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        url = URL(scope=scope)
        origin = f"{url.scheme}://{url.netloc}"
        settings = getattr(getattr(scope.get("app"), "state", None), "settings", None)

        async def rewrite(message: Message) -> None:
            if (
                settings is not None
                and message["type"] == "http.response.start"
                and message["status"] in _REDIRECTS
            ):
                headers = MutableHeaders(scope=message)
                location = headers.get("location", "")
                if location.startswith(origin) and location[len(origin) :][:1] in {"/", "?", ""}:
                    headers["location"] = url_on(settings.public_base_url, location[len(origin) :])
            await send(message)

        await self._app(scope, receive, rewrite)
