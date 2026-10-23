"""Composition root: builds the FastAPI application and wires adapters into it.

Run with ``uvicorn app.main:create_app --factory``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.adapters.clock import SystemClock
from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.api import booking_pages, health, pages
from app.api.security_headers import SecurityHeadersMiddleware
from app.config import Settings
from app.domain.ports import Clock
from app.domain.services import BookingService
from app.i18n import Translator, load_catalogues
from app.keys import management_token_key
from app.logging import configure_logging
from app.web.error_pages import register_error_pages
from app.web.protection import configure_rate_limits, limiter
from app.web.templates import STATIC_DIR


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    """Build the application.

    Args:
        settings: Explicit settings (tests); defaults to reading the environment.
        clock: Explicit clock (tests); defaults to the system clock.

    Returns:
        The FastAPI app with translator, booking service, rate limits and error pages. Its
        lifespan configures logging, opens the database engine on startup and disposes it
        on shutdown.
    """
    settings = settings or Settings()
    clock = clock or SystemClock()
    token_key = management_token_key(settings)
    configure_rate_limits(
        enabled=settings.rate_limit_enabled,
        bookings=settings.rate_limit_bookings,
        reads=settings.rate_limit_reads,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        app.state.engine = create_engine(settings.database_url)
        app.state.booking_service = BookingService(
            SqlUnitOfWork(app.state.engine), clock, token_key
        )
        try:
            yield
        finally:
            await app.state.engine.dispose()

    # Swagger UI and ReDoc load scripts from a CDN; only the JSON spec is served.
    app = FastAPI(title="OpenPlanner", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.limiter = limiter
    # Admin-edited overrides are wired in here once the database adapter exists (WP7).
    app.state.translator = Translator(load_catalogues())
    app.add_middleware(SecurityHeadersMiddleware)
    register_error_pages(app)
    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(booking_pages.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
