"""Composition root: builds the FastAPI application and wires adapters into it.

Run with ``uvicorn app.main:create_app --factory``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.adapters.db.engine import create_engine
from app.api import health, pages
from app.api.security_headers import SecurityHeadersMiddleware
from app.config import Settings
from app.i18n import Translator, load_catalogues
from app.logging import configure_logging
from app.web.templates import STATIC_DIR


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Args:
        settings: Explicit settings (tests); defaults to reading the environment.

    Returns:
        The FastAPI app with the translator loaded from the shipped catalogues. Its lifespan
        configures logging, opens the database engine on startup and disposes it on
        shutdown.
    """
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        app.state.engine = create_engine(settings.database_url)
        try:
            yield
        finally:
            await app.state.engine.dispose()

    # Swagger UI and ReDoc load scripts from a CDN; only the JSON spec is served.
    app = FastAPI(title="OpenPlanner", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    # Admin-edited overrides are wired in here once the database adapter exists (WP7).
    app.state.translator = Translator(load_catalogues())
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(health.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
