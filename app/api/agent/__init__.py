"""The machine API for contact-center bots, mounted at ``/api/agent/v1`` (ADR 0023).

A FastAPI sub-application with its own OpenAPI spec and JSON errors, so the citizen pages and
the public API are not affected. It exists only when ``AGENT_API_ENABLED`` is set; the
superuser's switch in the admin portal then decides whether it answers.

    oauth/token   client credentials → bearer token          (token)
    me, groups, groups/{slug}/availability                   (routes)
    openapi.json, docs   only while published                (docs)
"""

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.agent import docs, routes, token
from app.api.agent.errors import register_errors
from app.config import AGENT_API_PREFIX as PREFIX

VERSION = "1.0.0"


class NoStoreMiddleware:
    """Mark every response ``Cache-Control: no-store`` unless a route set its own."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Add the header to ``http.response.start``."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    message["headers"] = [*headers, (b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, receive, send_no_store)


def create_agent_api() -> FastAPI:
    """Build the machine API application; ``app.main`` mounts it and fills its state."""
    app = FastAPI(
        title="OpenPlanner agent API",
        version=VERSION,
        description=(
            "API for contact-center bots (Webex Contact Center AI Agent). OAuth 2.0 client "
            "credentials; no personal data in requests or responses."
        ),
        servers=[{"url": PREFIX}],
        root_path_in_servers=False,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(NoStoreMiddleware)
    register_errors(app)
    app.include_router(token.router)
    app.include_router(routes.router)
    app.include_router(docs.router)
    return app
