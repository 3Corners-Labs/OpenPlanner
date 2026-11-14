"""The app in-process behind an HTTP client, for the tests of the participant pages and the APIs.

The ``client`` fixture of ``tests/conftest.py`` is the app on the test's settings; a test that
needs other settings, a clock or integration adapters starts its own with ``app_client``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.ports import Clock
from app.main import create_app


@asynccontextmanager
async def app_client(
    settings: Settings, clock: Clock | None = None, integrations: Integrations | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's lifespan and yield a client that talks to it in-process."""
    app = create_app(settings, clock, integrations=integrations)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield client
