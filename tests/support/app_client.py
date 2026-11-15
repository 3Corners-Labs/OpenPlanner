"""The app in-process behind an HTTP client, for the tests of the pages and the APIs.

``served`` runs an app's lifespan and yields a client that talks to it; ``app_client`` does that
for a new app on the test's settings, as the ``client`` fixture of ``tests/conftest.py`` does.
``in_process`` is the transport underneath, also for another client on an app already running.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.ports import Clock
from app.main import create_app

PEER_PORT = 4711
"""The port of a client address a test gives (``peer``); only the address matters to the app."""


def in_process(app: FastAPI, peer: str | None = None) -> httpx.ASGITransport:
    """Return a transport to ``app`` in-process, with requests from ``peer`` if one is given."""
    if peer is None:
        return httpx.ASGITransport(app=app)
    return httpx.ASGITransport(app=app, client=(peer, PEER_PORT))


@asynccontextmanager
async def served(
    app: FastAPI, base_url: str = "http://testserver", peer: str | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    """Run ``app``'s lifespan and yield a client that talks to it in-process."""
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=in_process(app, peer), base_url=base_url) as client,
    ):
        yield client


@asynccontextmanager
async def app_client(
    settings: Settings, clock: Clock | None = None, integrations: Integrations | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    """Run a new app on ``settings`` and yield a client that talks to it in-process."""
    async with served(create_app(settings, clock, integrations=integrations)) as client:
        yield client
