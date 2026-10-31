"""ASGI middleware that runs every HTTP request in its own trace (ADR 0040, part 2).

Each request gets a fresh trace id, so its log lines and the audit events it writes share one
``trace.id``. An incoming ``traceparent`` is ignored on purpose: a public client could
otherwise make its requests share a trace with someone else's and muddle a trace filter. Admin
routes later bind the organisation and group they act on (``app.admin.authz``); public pages
under ``/g/{slug}`` bind their group (``bind_group_from_path``).

Each request also leaves one ``http.request`` trace line (ADR 0085 point 9) with its method,
the route template Starlette matched (``/g/{slug}``, never the raw path: ``/b/{token}``
carries the management token), the status and the duration; an unmatched path has an empty
route.
"""

from time import perf_counter

from fastapi import Request
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.domain.event_context import bind_level, trace_scope
from app.domain.scheduling import is_slug
from app.logging import tracer


class TraceMiddleware:
    """Wrap each HTTP request in ``trace_scope``; other scopes pass through."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the request inside a new trace, restored afterwards."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = perf_counter()
        status = 0

        async def send_traced(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        with trace_scope():
            try:
                await self.app(scope, receive, send_traced)
            finally:
                route = scope.get("route")
                tracer.trace(
                    "http.request",
                    method=str(scope.get("method", "")),
                    route=route.path if isinstance(route, Route) else "",
                    status=status,
                    ms=int((perf_counter() - started) * 1000),
                )


async def bind_group_from_path(request: Request) -> None:
    """Router dependency: tag the trace with the group slug of a ``/g/{slug}`` route.

    Only a well-formed slug is bound, so a crafted path never reaches the log as a group. The
    organisation is not looked up here; events the domain writes carry it anyway.
    """
    slug = request.path_params.get("slug")
    if isinstance(slug, str) and is_slug(slug):
        bind_level(None, slug)
