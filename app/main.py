"""Composition root: builds the FastAPI application and wires adapters into it.

Run with ``uvicorn app.main:create_app --factory``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.adapters.clock import SystemClock
from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import Integrations, build_integrations, secret_box
from app.adapters.oidc import ClaimMapping, OidcConfig, OidcIdentityProvider
from app.admin.routes import register_admin
from app.api import (
    booking_pages,
    brand_assets,
    direct_pages,
    health,
    join_pages,
    pages,
    webhooks,
)
from app.api.security_headers import SecurityHeadersMiddleware
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.branding_service import BrandingService
from app.domain.call_service import CallService
from app.domain.channel_service import ChannelSettingsService
from app.domain.integration import IntegrationService
from app.domain.messaging_service import MessagingService
from app.domain.organisations import OrganisationService
from app.domain.planning import PlanningService
from app.domain.ports import Clock, IdentityProvider
from app.domain.services import BookingService
from app.i18n import Translator, load_catalogues
from app.i18n.dates import configure_clock
from app.keys import management_token_key, session_secret
from app.logging import configure_logging
from app.web.call_labels import agent_start_label
from app.web.error_pages import register_error_pages
from app.web.protection import configure_rate_limits, limiter
from app.web.templates import STATIC_DIR


def oidc_provider(settings: Settings, http: httpx.AsyncClient) -> IdentityProvider | None:
    """Return the admin identity provider from the settings, or ``None`` if not configured."""
    if settings.oidc_discovery_url is None:
        return None
    mapping = ClaimMapping(
        role_claim=settings.oidc_role_claim,
        organisation_claim=settings.oidc_organisation_claim,
        group_claim=settings.oidc_group_claim,
        role_values={
            settings.oidc_role_superuser: Role.SUPERUSER,
            settings.oidc_role_org_admin: Role.ORG_ADMIN,
            settings.oidc_role_group_admin: Role.GROUP_ADMIN,
        },
    )
    secret = settings.oidc_client_secret.get_secret_value() if settings.oidc_client_secret else ""
    config = OidcConfig(
        discovery_url=settings.oidc_discovery_url,
        client_id=settings.oidc_client_id,
        client_secret=secret,
        scopes=settings.oidc_scopes,
        mapping=mapping,
    )
    return OidcIdentityProvider(config, http)


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    identity_provider: IdentityProvider | None = None,
    integrations: Integrations | None = None,
) -> FastAPI:
    """Build the application.

    Args:
        settings: Explicit settings (tests); defaults to reading the environment.
        clock: Explicit clock (tests); defaults to the system clock.
        identity_provider: Explicit identity provider (tests); defaults to OIDC from the
            settings, or none (admin login disabled).
        integrations: Explicit Webex/WxCC adapters (tests); defaults to the ones the settings
            configure, or none.

    Returns:
        The FastAPI app with translator, booking service, rate limits and error pages. Its
        lifespan configures logging, opens the database engine on startup and disposes it
        on shutdown.
    """
    settings = settings or Settings()
    clock = clock or SystemClock()
    token_key = management_token_key(settings)
    http = httpx.AsyncClient(timeout=10.0)
    configure_clock(settings.time_format)
    configure_rate_limits(
        enabled=settings.rate_limit_enabled,
        bookings=settings.rate_limit_bookings,
        reads=settings.rate_limit_reads,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        app.state.engine = create_engine(settings.database_url)
        uow = SqlUnitOfWork(app.state.engine, secret_box(settings))
        app.state.booking_service = BookingService(uow, clock, token_key)
        app.state.admin_service = AdminService(uow, clock)
        app.state.planning_service = PlanningService(uow, clock, app.state.admin_service)
        app.state.integration_service = IntegrationService(uow, clock, app.state.admin_service)
        app.state.branding_service = BrandingService(uow, clock, app.state.admin_service)
        app.state.channel_service = ChannelSettingsService(uow, clock, app.state.admin_service)
        app.state.messaging_service = MessagingService(
            uow, clock, app.state.admin_service, app.state.translator.locales
        )
        app.state.organisation_service = OrganisationService(
            uow, clock, app.state.admin_service, app.state.translator.locales
        )
        wired = integrations or build_integrations(settings, app.state.engine, http, clock)
        app.state.integrations = wired
        app.state.call_service = CallService(
            uow, app.state.integration_service, wired.video, wired.dispatcher, agent_start_label
        )
        try:
            yield
        finally:
            await http.aclose()
            await app.state.engine.dispose()

    # Swagger UI and ReDoc load scripts from a CDN; only the JSON spec is served.
    app = FastAPI(title="OpenPlanner", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.limiter = limiter
    app.state.identity_provider = identity_provider or oidc_provider(settings, http)
    # Admin-edited overrides are wired in here once the database adapter exists (WP7).
    app.state.translator = Translator(load_catalogues())
    # Admin session (ADR 0013): only sent to /admin, signed, HttpOnly, SameSite=Lax.
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret(settings),
        session_cookie="op_admin",
        path="/admin",
        max_age=settings.admin_session_hours * 3600,
        same_site="lax",
        https_only=settings.environment == "prod",
    )
    app.add_middleware(SecurityHeadersMiddleware)
    register_error_pages(app)
    register_admin(app)
    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(booking_pages.router)
    app.include_router(join_pages.router)
    app.include_router(direct_pages.router)
    app.include_router(brand_assets.router)
    app.include_router(webhooks.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
