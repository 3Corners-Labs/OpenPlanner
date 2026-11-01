"""Composition root: builds the FastAPI application and wires adapters into it.

Run with ``uvicorn app.main:create_app --factory``.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

import httpx
import structlog
from fastapi import Depends, FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.adapters.certificate_pem import CryptographyPemReader
from app.adapters.certificate_volume import FileCertificateVolume
from app.adapters.clock import SystemClock
from app.adapters.db.backup import database_path
from app.adapters.db.configuration_package import (
    SqliteConfigurationTransfer,
    portable_environment,
)
from app.adapters.db.engine import create_engine, set_slow_query_ms
from app.adapters.db.identity_providers import ProviderCredentialStore
from app.adapters.db.probes import ProbeReports
from app.adapters.db.reports import ReportStore
from app.adapters.db.text_overrides import CachedTextOverrides
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.discovery import GuardedDiscoveryFetcher
from app.adapters.event_sink import StdoutEventSink
from app.adapters.http_trace import traced_transport
from app.adapters.idp_registry import OrganisationProviders
from app.adapters.integrations import (
    Integrations,
    build_integrations,
    secret_box,
    webex_fingerprint,
)
from app.adapters.live_settings import LiveSettings, keep_current
from app.adapters.mail_dns import DnsTxtResolver
from app.adapters.oidc import Guard, OidcConfig, OidcIdentityProvider, claim_mapping
from app.adapters.releases import update_service
from app.adapters.senders import ConfiguredSenders, fallback_mail
from app.adapters.signing_keys import EcSigningKeys
from app.adapters.tls_probe import SslCertificateProbe
from app.admin.routes import register_admin
from app.api import (
    booking_pages,
    brand_assets,
    direct_pages,
    health,
    join_pages,
    pages,
    trial_pages,
    webhooks,
    widget_api,
    widget_script,
)
from app.api.agent import create_agent_api
from app.api.maintenance import participant_gate, register_maintenance
from app.api.security_headers import SecurityHeadersMiddleware
from app.api.trace import TraceMiddleware
from app.config import AGENT_API_PREFIX, Settings, environment, loose_secret_files
from app.domain.admin import AdminService
from app.domain.agenda_service import AgendaService
from app.domain.api_access import ApiAccessService
from app.domain.branding_service import BrandingService
from app.domain.call_retry import CallRetryService
from app.domain.call_service import CallService
from app.domain.channel_service import ChannelSettingsService
from app.domain.client_pseudonym import ClientPseudonyms
from app.domain.closure_service import ClosureService
from app.domain.configuration_service import ConfigurationPackageService
from app.domain.confirmation import ConfirmPolicy
from app.domain.contact_service import ContactService
from app.domain.diagnostics import DiagnosticsService
from app.domain.embedding_service import EmbeddingService
from app.domain.entitlements import ClaimMode
from app.domain.event_context import set_service
from app.domain.form_service import FormService
from app.domain.holiday_service import HolidayService
from app.domain.identity_provider_service import IdentityProviderService
from app.domain.idp_mapping import IdpMappingService, MappingSettings
from app.domain.integration import IntegrationService
from app.domain.integration_check import IntegrationCheckService
from app.domain.login_service import ClaimSettings, LoginService
from app.domain.maintenance_service import MaintenanceService
from app.domain.meeting_status_service import MeetingStatusService
from app.domain.messaging_service import MessagingService
from app.domain.organisation_text_service import OrganisationTextService
from app.domain.organisations import OrganisationService
from app.domain.planner_service import PlannerService
from app.domain.planning import PlanningService
from app.domain.platform_settings import PlatformSettingsStore
from app.domain.ports import (
    CertificateProbe,
    Clock,
    DiscoveryFetcher,
    IdentityProvider,
    MailDomainResolver,
    SigningKeys,
)
from app.domain.queue_service import QueueService
from app.domain.request_log import RequestEvents
from app.domain.roles import RoleService
from app.domain.scenario_service import ScenarioService
from app.domain.security_log import SecurityLog
from app.domain.services import BookingService
from app.domain.settings_service import SettingsService
from app.domain.simulation import SimulationService
from app.domain.system_log import SystemLog
from app.domain.tls import public_address
from app.domain.tls_check import CertificateStatus, TlsWatch
from app.domain.tls_source import CertificateSourceService
from app.domain.trial_call_service import TrialCallService
from app.i18n import Translator, load_catalogues
from app.i18n.dates import configure_clock
from app.keys import (
    agent_pseudonym_key,
    client_pseudonym_key,
    management_token_key,
    session_secret,
)
from app.logging import configure_logging, set_log_level, warn_loose_secret_files
from app.web.authz_labels import ROLE_LABELS
from app.web.call_labels import agent_start_label
from app.web.error_pages import register_error_pages
from app.web.organisation_settings import ClockScopeMiddleware
from app.web.protection import configure_rate_limits, limiter, set_rate_limits
from app.web.static_assets import ASSETS, VersionedStaticFiles


def _public_host(public_base_url: str) -> str | None:
    """Return the host an own certificate must cover: that of an ``https`` public address."""
    address = public_address(public_base_url)
    return address.host if address else None


def oidc_provider(
    settings: Settings,
    http: httpx.AsyncClient,
    current: Callable[[], Settings] | None = None,
) -> IdentityProvider | None:
    """Return the admin identity provider from the settings, or ``None`` if not configured.

    The claim mapping is read from ``current()`` at every login, so a mapping changed in the
    portal (ADR 0042 §8) applies at once; the client registration stays class A.
    """
    current = current or (lambda: settings)
    if settings.oidc_discovery_url is None:
        return None
    secret = settings.oidc_client_secret.get_secret_value() if settings.oidc_client_secret else ""
    config = OidcConfig(
        discovery_url=settings.oidc_discovery_url,
        client_id=settings.oidc_client_id,
        client_secret=secret,
        scopes=settings.oidc_scopes,
        mapping=lambda: claim_mapping(
            current().login_values, current().oidc_entitlements_max_values
        ),
    )
    return OidcIdentityProvider(config, http)


def _confirm_policy(settings: Settings) -> ConfirmPolicy:
    """Return the rules for confirming a booking from one organisation's settings (ADR 0056)."""
    return ConfirmPolicy.from_settings(
        window_hours=settings.booking_confirm_window_hours,
        cutoff_hours=settings.booking_confirm_cutoff_hours,
        reminders=settings.booking_confirm_reminders,
        min_minutes=settings.booking_confirm_min_minutes,
    )


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    identity_provider: IdentityProvider | None = None,
    integrations: Integrations | None = None,
    certificate_probe: CertificateProbe | None = None,
    mail_domain_resolver: MailDomainResolver | None = None,
    discovery_fetcher: DiscoveryFetcher | None = None,
    signing_keys: SigningKeys | None = None,
    organisation_providers: OrganisationProviders | None = None,
) -> FastAPI:
    """Build the application.

    Args:
        settings: Explicit settings (tests); defaults to reading the environment.
        clock: Explicit clock (tests); defaults to the system clock.
        identity_provider: Explicit identity provider (tests); defaults to OIDC from the
            settings, or none (admin login disabled).
        integrations: Explicit Webex/WxCC adapters (tests); defaults to the ones the settings
            configure, or none.
        certificate_probe: Explicit TLS probe for *Check now* (tests); defaults to a real
            handshake.
        mail_domain_resolver: Explicit DNS lookup for the sender domain check (tests);
            defaults to the host's resolver (ADR 0073).
        discovery_fetcher: Explicit fetcher of discovery documents (tests); defaults to a
            guarded fetch (ADR 0083 point 8).
        signing_keys: Explicit key generator (tests); defaults to EC P-256 pairs.
        organisation_providers: Explicit registry of organisation provider adapters (tests);
            defaults to one built from the database.

    Returns:
        The FastAPI app with translator, booking service, rate limits and error pages. Its
        lifespan configures logging, opens the database engine on startup and disposes it
        on shutdown.
    """
    settings = settings or Settings()
    clock = clock or SystemClock()
    token_key = management_token_key(settings)
    http = httpx.AsyncClient(timeout=10.0, transport=traced_transport(None))
    configure_clock(settings.time_format)
    configure_rate_limits(
        enabled=settings.rate_limit_enabled,
        bookings=settings.rate_limit_bookings,
        reads=settings.rate_limit_reads,
        unknown_links=settings.rate_limit_unknown_links,
        agent_tokens=settings.rate_limit_agent_tokens,
        agent_calls=settings.rate_limit_agent_calls,
    )
    # Machine API (ADR 0023): only mounted when the deployment switches it on.
    agent_api = create_agent_api() if settings.agent_api_enabled else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        set_service("web")
        configure_logging(settings.log_level, envelope=settings.event_envelope)
        warn_loose_secret_files(loose_secret_files(environment()))
        app.state.engine = create_engine(settings.database_url)
        app.state.http = http  # ADR 0082: the Service App check runs the probes from here
        # ADR 0034: committed audit events also go to stdout for the log platform.
        uow = SqlUnitOfWork(
            app.state.engine, secret_box(settings), StdoutEventSink(settings.event_envelope)
        )
        # ADR 0068: every request to Webex and WxCC becomes a system event of the booking.
        request_log = RequestEvents(uow, clock, settings.app_version)

        def apply(current: Settings) -> None:
            app.state.settings = current
            if agent_api is not None:
                agent_api.state.settings = current
            set_rate_limits(
                bookings=current.rate_limit_bookings,
                reads=current.rate_limit_reads,
                unknown_links=current.rate_limit_unknown_links,
                agent_tokens=current.rate_limit_agent_tokens,
                agent_calls=current.rate_limit_agent_calls,
            )
            set_log_level(current.log_level)
            set_slow_query_ms(current.sql_slow_ms)
            configure_clock(current.time_format)
            # ADR 0042 step 6: the release repository and its token can change in the portal.
            app.state.update_service = update_service(current, uow, clock, http, lookup=False)
            # ADR 0070: a changed certificate source (portal, CLI or lock) reaches the proxy.
            source = getattr(app.state, "certificate_source", None)
            if source is not None:
                app.state.certificate_sync = asyncio.create_task(source.sync())
            # ADR 0042 step 6b: so can the Webex Service App and the WxCC host.
            wired_with = getattr(app.state, "webex_fingerprint", None)
            if wired_with is not None and wired_with != webex_fingerprint(current):
                wire_integrations(current)

        def wire_integrations(current: Settings) -> None:
            wired = integrations or build_integrations(
                current,
                app.state.engine,
                http,
                clock,
                app.state.security_log,
                request_log,
                probe_timeout=lambda: timedelta(seconds=live().integration_probe_timeout_seconds),
            )
            app.state.integrations = wired
            app.state.call_service = CallService(
                uow,
                app.state.integration_service,
                wired.video,
                wired.dispatcher,
                agent_start_label,
            )
            app.state.trial_call_service = TrialCallService(
                uow, wired.video, app.state.integration_service, clock
            )
            app.state.integration_check_service = IntegrationCheckService(
                uow,
                clock,
                app.state.admin_service,
                wired.checker,
                SystemLog(uow, clock),
                version=settings.app_version,
                heartbeat_max_age=timedelta(seconds=settings.worker_heartbeat_max_age_seconds),
                overdue_after=lambda: timedelta(minutes=live().job_overdue_minutes),
            )
            # Explicit adapters (tests) are never rebuilt.
            app.state.webex_fingerprint = None if integrations else webex_fingerprint(current)

        def live() -> Settings:
            now: Settings = app.state.settings
            return now

        def per_organisation(organisation_id: int) -> Settings:
            settings_of: Settings = app.state.live_settings.for_organisation(organisation_id)
            return settings_of

        # ADR 0042 §2, §6: portal values on top of the environment, reloaded when they change.
        # Values read on each request follow at once; the services below read theirs through
        # ``live`` each time they need them.
        app.state.live_settings = LiveSettings(
            settings, environment(), PlatformSettingsStore(uow), clock, apply
        )
        await app.state.live_settings.refresh()
        reloader = asyncio.create_task(keep_current(app.state.live_settings))
        app.state.security_log = SecurityLog(
            uow,
            clock,
            lambda: timedelta(minutes=live().security_event_window_minutes),
            max_sources=lambda: live().security_event_max_sources,
        )
        app.state.booking_service = BookingService(
            uow,
            clock,
            token_key,
            phone_region=lambda org: per_organisation(org).phone_default_region,
            confirm_policy=lambda org: _confirm_policy(per_organisation(org)),
        )
        app.state.admin_service = AdminService(uow, clock)
        # ADR 0049: claims become grants per request; the mode is class B, the prefix C.
        app.state.login_service = LoginService(
            uow,
            app.state.admin_service,
            lambda: ClaimSettings(
                ClaimMode(settings.oidc_claim_mode), live().oidc_entitlement_prefix
            ),
        )
        # ADR 0083 step 2: organisation identity providers, their adapters and registration.
        lab = settings.environment != "prod"
        providers = organisation_providers or OrganisationProviders(
            ProviderCredentialStore(app.state.engine, secret_box(settings)),
            http,
            guard=Guard(allow_private=lab),
            max_entitlements=lambda: live().oidc_entitlements_max_values,
        )
        app.state.organisation_providers = providers
        app.state.identity_provider_service = IdentityProviderService(
            uow,
            clock,
            app.state.admin_service,
            discovery_fetcher or GuardedDiscoveryFetcher(http, allow_private=lab),
            signing_keys or EcSigningKeys(),
            limit=lambda: live().idp_max_per_organisation,
            platform_discovery_url=lambda: settings.oidc_discovery_url,
            on_change=providers.invalidate,
        )
        app.state.idp_mapping_service = IdpMappingService(
            uow,
            clock,
            app.state.admin_service,
            lambda: MappingSettings(
                ClaimMode(settings.oidc_claim_mode),
                live().oidc_entitlement_claim,
                live().oidc_entitlement_prefix,
                live().idp_mapping_refused_window_days,
            ),
        )
        app.state.form_service = FormService(
            uow, clock, app.state.admin_service, lambda: live().booking_form_max_custom_fields
        )
        # ADR 0042 step 4: class C settings changed in the portal, checked against the lock.
        app.state.settings_service = SettingsService(
            uow,
            clock,
            app.state.admin_service,
            locked=app.state.live_settings.locked,
            combination=app.state.live_settings.combination_problem,
            environment_value=app.state.live_settings.environment_value,
            production=settings.environment == "prod",
        )
        # ADR 0089: configuration packages, exported and imported on Advanced settings.
        app.state.configuration_packages = ConfigurationPackageService(
            SqliteConfigurationTransfer(
                database_path(settings.database_url),
                secret_box(settings),
                installation=settings.installation_name or "",
                app_version=settings.app_version or "",
                environment=lambda: portable_environment(settings),
                now=clock.now,
            ),
            clock,
            app.state.admin_service,
            max_bytes=lambda: live().configuration_package_max_mb * 1024 * 1024,
        )
        app.state.certificate_status = CertificateStatus(uow)
        # ADR 0070: the certificate source of the bundled proxy, with the pair handed to the
        # proxy through the shared volume whenever the effective source changes.
        app.state.certificate_source = CertificateSourceService(
            uow,
            clock,
            app.state.admin_service,
            app.state.settings_service,
            CryptographyPemReader(),
            FileCertificateVolume(settings.tls_portal_certificate_dir),
            TlsWatch(
                uow,
                clock,
                certificate_probe or SslCertificateProbe(),
                SystemLog(uow, clock),
                public_base_url=settings.public_base_url,
                interval=lambda: timedelta(hours=live().tls_check_interval_hours),
                warn_within=lambda: timedelta(days=live().tls_expiry_warning_days),
                repeat=lambda: timedelta(hours=live().tls_alert_repeat_hours),
            ),
            host=lambda: _public_host(settings.public_base_url),
            bundled=lambda: settings.tls_proxy_bundled,
            source=lambda: live().tls_certificate_source,
        )
        app.state.certificate_sync = asyncio.create_task(app.state.certificate_source.sync())
        # ADR 0038: read on every admin change (and participant write), cached for a few seconds.
        app.state.maintenance_service = MaintenanceService(
            uow,
            clock,
            app.state.admin_service,
            environment=settings.maintenance_mode,
            cache=lambda: timedelta(seconds=live().maintenance_cache_seconds),
        )
        app.state.planning_service = PlanningService(uow, clock, app.state.admin_service)
        app.state.queue_service = QueueService(
            uow, clock, app.state.admin_service, agent_pseudonym_key(settings)
        )
        app.state.integration_service = IntegrationService(
            uow,
            clock,
            app.state.admin_service,
            settings.app_version,
            probes=ProbeReports(ReportStore(app.state.engine)),
        )
        app.state.meeting_status_service = MeetingStatusService(
            uow,
            clock,
            app.state.integration_service,
            lambda: app.state.integrations.video,
            lambda: timedelta(seconds=live().meeting_status_refresh_seconds),
            settings.app_version,
        )
        app.state.call_retry_service = CallRetryService(uow, clock, app.state.admin_service)
        app.state.branding_service = BrandingService(uow, clock, app.state.admin_service)
        app.state.contact_service = ContactService(uow, clock, app.state.admin_service)
        app.state.scenario_service = ScenarioService(uow, clock, app.state.admin_service)
        app.state.embedding_service = EmbeddingService(
            uow,
            clock,
            app.state.admin_service,
            max_origins=lambda: live().embedding_max_origins,
            allow_local=settings.environment != "prod",
        )
        app.state.holiday_service = HolidayService(uow, clock, app.state.admin_service)
        app.state.agenda_service = AgendaService(
            uow,
            clock,
            app.state.admin_service,
            lambda org: per_organisation(org).shortfall_horizon_days,
        )
        app.state.diagnostics_service = DiagnosticsService(
            uow,
            clock,
            app.state.admin_service,
            lambda: timedelta(hours=live().diagnostic_mode_hours),
        )
        # Test emails go through exactly the senders the worker uses (ADR 0022).
        senders = ConfiguredSenders(uow, http, lambda: fallback_mail(live()))
        # The sender domain check looks up the fallback sender's domain when an organisation
        # has no SMTP settings of its own (ADR 0073).
        app.state.channel_service = ChannelSettingsService(
            uow,
            clock,
            app.state.admin_service,
            senders,
            mail_domain_resolver or DnsTxtResolver(),
            lambda: live().mail_from if live().smtp_host else None,
        )
        text_overrides.attach(uow)
        app.state.organisation_text_service = OrganisationTextService(
            uow,
            clock,
            app.state.admin_service,
            app.state.translator.locales,
            {
                code: {
                    key: app.state.translator.gettext(code, key)
                    for key in ROLE_LABELS.values()
                    if key.startswith("authz.role.")
                }
                for code in app.state.translator.locales
            },
            on_change=text_overrides.invalidate,
        )
        app.state.messaging_service = MessagingService(
            uow,
            clock,
            app.state.admin_service,
            app.state.translator.locales,
            on_change=text_overrides.invalidate,
        )
        app.state.organisation_service = OrganisationService(
            uow, clock, app.state.admin_service, app.state.translator.locales
        )
        app.state.closure_service = ClosureService(
            uow, clock, app.state.admin_service, app.state.booking_service
        )
        app.state.planner_service = PlannerService(
            uow,
            clock,
            app.state.admin_service,
            app.state.booking_service,
            lambda: live().planner_changes_alert_per_hour,
            rebook_days=lambda org: per_organisation(org).planner_rebook_days,
            reminder_days=lambda org: per_organisation(org).planner_rebook_reminder_days,
        )
        app.state.role_service = RoleService(
            uow,
            clock,
            app.state.admin_service,
            (
                settings.oidc_role_superuser,
                settings.oidc_role_org_admin,
                settings.oidc_role_group_admin,
                settings.oidc_role_auditor,
                settings.oidc_role_agent,
            ),
        )
        app.state.api_access = ApiAccessService(
            uow, clock, app.state.admin_service, available=settings.agent_api_enabled
        )
        # ADR 0029: the web process only reads checks and stores requests; no release lookup.
        app.state.update_service = update_service(live(), uow, clock, http, lookup=False)
        # ADR 0067: a version change nobody requested in the portal still leaves a trace.
        try:
            await app.state.update_service.note_running(settings.app_version, settings.app_commit)
        except Exception as exc:  # an old schema without the table must not stop the start
            structlog.get_logger().warning("updates.version_note_failed", error=type(exc).__name__)
        if agent_api is not None:
            for name in (
                "api_access",
                "booking_service",
                "translator",
                "limiter",
                "clock",
                "maintenance_service",
                "security_log",
                "client_pseudonyms",
            ):
                setattr(agent_api.state, name, getattr(app.state, name))
            agent_api.state.settings = app.state.settings
        wire_integrations(live())
        # The simulator plays platform events through our own webhooks: only when switched on
        # (SIMULATOR_ENABLED), which the settings refuse in production.
        app.state.simulation_service = SimulationService(
            uow,
            clock,
            app.state.admin_service,
            app.state.integration_service,
            enabled=settings.simulator_enabled and settings.environment != "prod",
        )
        try:
            yield
        finally:
            reloader.cancel()
            with suppress(asyncio.CancelledError):
                await reloader
            await http.aclose()
            await app.state.engine.dispose()

    # Swagger UI and ReDoc load scripts from a CDN; only the JSON spec is served.
    app = FastAPI(title="OpenPlanner", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.limiter = limiter
    # ADR 0053: security events name callers by pseudonym, never by address.
    app.state.client_pseudonyms = ClientPseudonyms(client_pseudonym_key(settings))
    app.state.identity_provider = identity_provider or oidc_provider(
        settings, http, lambda: app.state.settings
    )
    # ADR 0074: the organisations' own texts; the source reads the database once the lifespan
    # has opened it (until then, and in tools without one, the shipped texts apply).
    text_overrides = CachedTextOverrides()
    app.state.translator = Translator(load_catalogues(), text_overrides)
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
    # Outermost: every request, its log lines and its audit events share one trace (ADR 0040).
    app.add_middleware(TraceMiddleware)
    # ADR 0042 step 5: one clock scope per request; routes fill in the organisation's style.
    app.add_middleware(ClockScopeMiddleware)
    register_error_pages(app)
    register_admin(app)
    # ADR 0038: the fixed maintenance rules of app.api.maintenance guard every public router.
    register_maintenance(app)
    gate = [Depends(participant_gate)]
    for public_router in (
        health.router,
        pages.router,
        booking_pages.router,
        join_pages.router,
        trial_pages.router,
        direct_pages.router,
        brand_assets.router,
        webhooks.router,
        widget_api.texts_router,
        widget_api.router,
        widget_script.router,
    ):
        app.include_router(public_router, dependencies=gate)
    if agent_api is not None:
        app.mount(AGENT_API_PREFIX, agent_api)
    app.mount("/static", VersionedStaticFiles(ASSETS), name="static")
    return app
