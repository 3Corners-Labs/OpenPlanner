"""ASGI middleware that runs every HTTP request in its own trace (ADR 0040, part 2).

Each request gets a fresh trace id, so its log lines and the audit events it writes share one
``trace.id``. An incoming ``traceparent`` is ignored on purpose: a public client could
otherwise make its requests share a trace with someone else's and muddle a trace filter. Admin
routes later bind the organisation and group they act on (``app.admin.authz``); public pages
under ``/g/{slug}`` bind their group (``bind_group_from_path``).
"""

from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.domain.event_context import bind_level, trace_scope
from app.domain.scheduling import is_slug


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
        with trace_scope():
            await self.app(scope, receive, send)


async def bind_group_from_path(request: Request) -> None:
    """Router dependency: tag the trace with the group slug of a ``/g/{slug}`` route.

    Only a well-formed slug is bound, so a crafted path never reaches the log as a group. The
    organisation is not looked up here; events the domain writes carry it anyway.
    """
    slug = request.path_params.get("slug")
    if isinstance(slug, str) and is_slug(slug):
        bind_level(None, slug)
