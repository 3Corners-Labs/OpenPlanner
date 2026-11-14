"""Composition root: builds the FastAPI application and wires adapters into it.

Run with ``uvicorn app.main:create_app --factory``.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from functools import partial

import httpx
import structlog
from fastapi import Depends, FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.adapters import internal_ca
from app.adapters.alert_senders import BotSpacePoster, ConfiguredAlertSenders, alert_bots
from app.adapters.certificate_pem import CryptographyPemReader
from app.adapters.certificate_volume import FileCertificateVolume
from app.adapters.clock import SystemClock
from app.adapters.configuration_context import document_service
from app.adapters.db.backup import database_path
from app.adapters.db.configuration_package import (
    SqliteConfigurationTransfer,
)
from app.adapters.db.engine import create_engine, set_slow_query_ms
from app.adapters.db.identity_providers import ProviderCredentialStore
from app.adapters.db.log_destinations import LogDestinationCredentialStore
from app.adapters.db.probes import ProbeReports
from app.adapters.db.reports import ReportStore
from app.adapters.db.text_overrides import CachedTextOverrides
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.discovery import GuardedDiscoveryFetcher
from app.adapters.event_sink import StdoutEventSink
from app.adapters.heartbeat import read_heartbeat
from app.adapters.http_trace import traced_transport
from app.adapters.idp_registry import OrganisationProviders
from app.adapters.integrations import (
    Integrations,
    build_integrations,
    sealed_connections,
    secret_box,
    webex_fingerprint,
)
from app.adapters.keycloak_directory import DirectoryAddresses, directory_from_settings
from app.adapters.live_settings import LiveSettings, keep_current
from app.adapters.log_senders import build_sender
from app.adapters.log_shipping import LogShipper, LogTester, SenderFactory, ship_settings
from app.adapters.mail_dns import DnsTxtResolver
from app.adapters.oidc import Guard, OidcConfig, OidcIdentityProvider, claim_mapping
from app.adapters.releases import update_service
from app.adapters.senders import ConfiguredSenders, fallback_mail, fallback_sender
from app.adapters.signing_keys import EcSigningKeys
from app.adapters.staff_addresses import StoredAddresses
from app.admin.display_routes import router as display_router
from app.admin.reviewed_documents import ReviewedDocuments
from app.admin.routes import register_admin
from app.admin.session import session_cookie_for
from app.api import (
    booking_pages,
    brand_assets,
    call_pages,
    direct_pages,
    health,
    immediate_pages,
    instance,
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
from app.domain.activity_alert import CHANGES_ALERT, REVEALS_ALERT
from app.domain.address_check import AddressStatus, InstanceService
from app.domain.admin import AdminService
from app.domain.admin_session_service import AdminSessionService
from app.domain.agenda_service import AgendaService
from app.domain.alert_delivery import AlertTargetService
from app.domain.alerts import AlertService
from app.domain.api_access import ApiAccessService
from app.domain.branding_service import BrandingService
from app.domain.call_retry import CallRetryService
from app.domain.call_service import CallService
from app.domain.call_token_service import CallTokenService
from app.domain.capacity_service import CapacityService
from app.domain.change_request_service import ChangePolicy, ChangeRequestService
from app.domain.channel_service import ChannelSettingsService
from app.domain.client_pseudonym import ClientPseudonyms
from app.domain.closure_service import ClosureService
from app.domain.configuration_service import ConfigurationPackageService
from app.domain.contact_center_connection import ContactCenterConnectionService
from app.domain.contact_service import ContactService
from app.domain.csv_export import ExportLimits
from app.domain.diagnostics import DiagnosticsService
from app.domain.embedding_service import EmbeddingService
from app.domain.entitlements import ClaimMode
from app.domain.event_context import set_service
from app.domain.fallback import FallbackService
from app.domain.flow_service import FlowService
from app.domain.form_service import FormService
from app.domain.holiday_service import HolidayService
from app.domain.identity_provider_service import IdentityProviderService
from app.domain.idp_mapping import IdpMappingService, MappingSettings
from app.domain.integration import IntegrationService
from app.domain.integration_check import IntegrationCheckService
from app.domain.integration_health import IntegrationHealthService
from app.domain.log_destination_service import LogDestinationService
from app.domain.login_service import ClaimSettings, LoginService
from app.domain.maintenance_service import MaintenanceService
from app.domain.meeting_status_service import MeetingStatusService
from app.domain.messaging_service import MessagingService
from app.domain.mfa import MfaPolicy
from app.domain.organisation_text_service import OrganisationTextService
from app.domain.organisations import OrganisationService
from app.domain.overview_layout import OverviewLayoutService
from app.domain.participant_contact import ParticipantContactService
from app.domain.participant_link_service import ParticipantLinkService
from app.domain.planning import PlanningService
from app.domain.platform_settings import PlatformSettingsStore
from app.domain.pool_planning import PoolPlanningService
from app.domain.ports import (
    CertificateProbe,
    Clock,
    DiscoveryFetcher,
    IdentityProvider,
    MailDomainResolver,
    SigningKeys,
    StaffDirectory,
)
from app.domain.proxy_watch import ProxyHeaderWatch
from app.domain.queue_service import QueueService
from app.domain.readiness import ReadinessService
from app.domain.report_digest_service import DigestAdmin
from app.domain.request_dispatch import AttemptEvents, own_connection_of
from app.domain.request_log import RequestEvents
from app.domain.roles import RoleService
from app.domain.routing_service import RoutingService
from app.domain.scenario_service import ScenarioService
from app.domain.security_log import SecurityLog
from app.domain.settings_service import SettingsService
from app.domain.simulation import SimulationService
from app.domain.skill_service import SkillService
from app.domain.staff_directory_service import DirectorySettings, StaffDirectoryService
from app.domain.staff_lifecycle import StaffLifecycleService
from app.domain.staff_usage import StaffUsage
from app.domain.system_log import SystemLog
from app.domain.tls import public_address
from app.domain.tls_check import CertificateStatus, TlsWatch
from app.domain.tls_source import CertificateSourceService
from app.domain.trial_call_service import TrialCallService
from app.domain.usage_api_service import UsageApiLimits, UsageApiService
from app.domain.usage_export_service import UsageExportService
from app.domain.usage_report_service import UsageReportService
from app.domain.wallboard_link import parse_networks
from app.domain.wallboard_link_service import WallboardLinkService
from app.domain.wallboard_service import WallboardService
from app.i18n import Translator, load_catalogues
from app.i18n.dates import configure_clock
from app.keys import (
    agent_pseudonym_key,
    agent_pseudonym_key_previous,
    client_pseudonym_key,
    guest_pseudonym_keys,
    management_token_key,
    management_token_key_previous,
    session_secret,
)
from app.logging import configure_logging, set_line_tap, set_log_level, warn_loose_secret_files
from app.service_wiring import booking_service, planner_service, request_services, request_work
from app.web.alert_text import alert_composer
from app.web.authz_labels import ROLE_LABELS
from app.web.call_labels import agent_start_label
from app.web.conversation_opening import conversation_opening
from app.web.error_pages import register_error_pages
from app.web.fallback_text import SpaceTexts
from app.web.organisation_settings import ClockScopeMiddleware
from app.web.protection import RateLimits, configure_rate_limits, limiter, set_rate_limits
from app.web.proxy_headers import ProxyHeaderMiddleware
from app.web.public_links import PublicRedirectMiddleware
from app.web.static_assets import ASSETS, VersionedStaticFiles
from app.web.webhook_ledger import install_webhook_ledger
from app.web.widgets import WIDGETS


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
        expected_issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=secret,
        scopes=settings.oidc_scopes,
        mfa_policy=MfaPolicy.REQUIRE if settings.admin_mfa_required else MfaPolicy.OFF,
        acr_values=settings.oidc_acr_values,
        mfa_amr_values=settings.oidc_mfa_amr_values,
        mfa_acr_values=settings.oidc_mfa_acr_values,
        mapping=lambda: claim_mapping(
            current().login_values, current().oidc_entitlements_max_values
        ),
    )
    return OidcIdentityProvider(config, http)


def _change_policy(settings: Settings) -> ChangePolicy:
    """Return one organisation's settings for a bot's change requests (ADR 0023 step 2).

    Switched on only where the deployment also runs the machine API (``AGENT_API_ENABLED``).
    """
    return ChangePolicy(
        settings.takes_change_requests,
        settings.agent_change_request_minutes,
        settings.agent_change_request_cooldown_minutes,
    )


def _keycloak_directory(settings: Settings) -> StaffDirectory | None:
    """Return the staff directory of the bundled Keycloak, or ``None`` without its settings."""
    return directory_from_settings(settings)


BUNDLED_REALM_PATH = "/realms/openplanner/"
"""Where the bundled provider's realm sits in its discovery URL (ADR 0090, the realm template)."""


def _directory_missing(settings: Settings) -> tuple[str, ...]:
    """Name the directory settings an incomplete bundled provider lacks (ADR 0106 point 3).

    The bundled provider is recognised by its realm in the discovery URL, or by one of the two
    directory settings being set. Any other provider has no directory by design and lacks
    nothing. Returns setting names only, never a value.
    """
    present = {
        "KEYCLOAK_ADMIN_URL": bool(settings.keycloak_admin_url),
        "KEYCLOAK_DIRECTORY_CLIENT_SECRET": settings.keycloak_directory_client_secret is not None,
    }
    bundled = BUNDLED_REALM_PATH in (settings.oidc_discovery_url or "") or any(present.values())
    return tuple(name for name, ok in present.items() if bundled and not ok)


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
    staff_directory: StaffDirectory | None = None,
    log_sender_factory: SenderFactory | None = None,
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
        staff_directory: Explicit staff directory (tests); defaults to the bundled Keycloak's
            admin API when ``KEYCLOAK_ADMIN_URL`` is set, else none (ADR 0095).
        log_sender_factory: Explicit builder of the log destination senders (tests); defaults
            to the real syslog, Splunk, Elastic and HTTP adapters.

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
    configure_rate_limits(RateLimits.from_settings(settings), enabled=settings.rate_limit_enabled)
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
            app.state.engine,
            secret_box(settings),
            StdoutEventSink(settings.event_envelope),
            usage_enabled=lambda: live().reporting_enabled,  # ADR 0117, read at each write
            usage_to_log=lambda: live().reporting_usage_events_to_log,  # point 15, read live
        )
        # ADR 0068: every request to Webex and WxCC becomes a system event of the booking.
        request_log = RequestEvents(uow, clock, settings.app_version)

        def apply(current: Settings) -> None:
            app.state.settings = current
            if agent_api is not None:
                agent_api.state.settings = current
            set_rate_limits(RateLimits.from_settings(current))
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
                wired.dispatchers,
                agent_start_label,
                app.state.fallback_service,
                wired.organisations,
                partial(conversation_opening, app.state.translator),
                lambda: app.state.settings.embedded_call_enabled,
            )
            app.state.trial_call_service = TrialCallService(
                uow, wired.video, app.state.integration_service, clock
            )
            # ADR 0123 point 6: the call page's guest token; rebuilt with the video provider.
            app.state.call_token_service = CallTokenService(
                uow,
                wired.video,
                app.state.integration_service,
                clock,
                lambda: guest_pseudonym_keys(settings),
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
        # ADR 0085: copies of the log lines go to the destinations the superuser configured.
        shipper = LogShipper(
            LogDestinationCredentialStore(app.state.engine, secret_box(settings)),
            log_sender_factory or build_sender,
            uow,
            clock,
            "web",
            lambda: ship_settings(live()),
            enabled=lambda: live().log_shipping_enabled,
            window=lambda: timedelta(minutes=live().security_event_window_minutes),
        )
        shipping_stop = asyncio.Event()
        shipping = asyncio.create_task(shipper.run(shipping_stop))
        set_line_tap(shipper.offer)
        app.state.security_log = SecurityLog(
            uow,
            clock,
            lambda: timedelta(minutes=live().security_event_window_minutes),
            max_sources=lambda: live().security_event_max_sources,
        )
        install_webhook_ledger(app, uow, clock, live)  # ADR 0124 point 10: the gate's ledger
        app.state.booking_service = booking_service(uow, clock, settings, app.state.live_settings)
        # ADR 0023 step 2: a bot's change request, confirmed by the participant.
        previous_key = management_token_key_previous(settings)
        app.state.change_request_service = ChangeRequestService(
            uow,
            clock,
            app.state.booking_service,
            (token_key, *([previous_key] if previous_key else [])),
            policy=lambda org: _change_policy(per_organisation(org)),
            phone_region=lambda org: per_organisation(org).phone_default_region,
        )
        app.state.participant_link_service = ParticipantLinkService(uow, clock)
        # ADR 0044: the thresholds of the hourly alerts, once, for every writer they count.
        app.state.admin_service = AdminService(
            uow,
            clock,
            app.state.live_settings.closure_retention,
            {
                CHANGES_ALERT: lambda: live().planner_changes_alert_per_hour,
                REVEALS_ALERT: lambda: live().contact_reveals_alert_per_hour,
            },
        )
        app.state.usage_report_service = UsageReportService(
            uow,
            app.state.admin_service,
            clock,
            lambda: live().reporting_min_cell_size,
            lambda organisation: per_organisation(organisation).reporting_org_min_cell_size,
            lambda organisation: per_organisation(organisation).reporting_staff_view_enabled,
        )
        app.state.wallboard_service = WallboardService(
            uow,
            app.state.usage_report_service,
            clock,
            lambda organisation: (
                per_organisation(organisation).reporting_wallboard_wait_alert_minutes
            ),
        )
        app.state.wallboard_link_service = WallboardLinkService(
            uow,
            app.state.admin_service,
            clock,
            lambda organisation: (
                per_organisation(organisation).reporting_wallboard_display_links_enabled
            ),
            lambda organisation: parse_networks(
                per_organisation(organisation).reporting_wallboard_link_networks
            ),
            lambda: live().reporting_wallboard_link_max_days,
        )
        app.state.digest_admin = DigestAdmin(
            uow,
            app.state.admin_service,
            clock,
            lambda: live().reporting_digest_max_subscriptions,
            StoredAddresses(uow, DirectoryAddresses(_keycloak_directory(settings))),
        )
        app.state.usage_export_service = UsageExportService(
            app.state.usage_report_service,
            app.state.admin_service,
            lambda: ExportLimits(
                live().reporting_export_max_rows, live().reporting_export_max_days
            ),
        )
        app.state.log_destination_service = LogDestinationService(
            uow,
            clock,
            app.state.admin_service,
            production=settings.environment == "prod",
            shipping_enabled=lambda: live().log_shipping_enabled,
            tester=LogTester(
                LogDestinationCredentialStore(app.state.engine, secret_box(settings)),
                log_sender_factory or build_sender,
                clock,
                "web",
                lambda: ship_settings(live()),
            ),
        )
        app.state.admin_session_service = AdminSessionService(uow, clock)
        app.state.staff_lifecycle_service = StaffLifecycleService(uow, clock)
        # ADR 0049: claims become grants per request; the mode is class B, the prefix C.
        app.state.login_service = LoginService(
            uow,
            app.state.admin_service,
            lambda: ClaimSettings(
                ClaimMode(settings.oidc_claim_mode), live().oidc_entitlement_prefix
            ),
            organisation_idps=lambda: settings.organisation_idps_enabled,
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
        txt_resolver = mail_domain_resolver or DnsTxtResolver()
        app.state.identity_provider_service = IdentityProviderService(
            uow,
            clock,
            app.state.admin_service,
            discovery_fetcher or GuardedDiscoveryFetcher(http, allow_private=lab),
            signing_keys or EcSigningKeys(),
            limit=lambda: live().idp_max_per_organisation,
            platform_discovery_url=lambda: settings.oidc_discovery_url,
            on_change=providers.invalidate,
            resolver=txt_resolver,
            login_suffix=lambda: live().bundled_idp_login_suffix,
            grace=lambda: timedelta(days=live().idp_domain_grace_days),
            routing=lambda: settings.organisation_idps_enabled,
            production=settings.environment == "prod",
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
        app.state.staff_directory_service = StaffDirectoryService(
            uow,
            clock,
            app.state.admin_service,
            staff_directory if staff_directory is not None else _keycloak_directory(settings),
            lambda: DirectorySettings(
                live().staff_directory_enabled,
                live().oidc_entitlement_prefix,
                live().staff_invite_expiry_days,
                live().bundled_idp_login_suffix,
            ),
            () if staff_directory is not None else _directory_missing(settings),
        )
        app.state.form_service = FormService(
            uow, clock, app.state.admin_service, lambda: live().booking_form_max_custom_fields
        )
        app.state.routing_service = RoutingService(
            uow, clock, app.state.admin_service, lambda: live().max_subjects_per_group
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
        # ADR 0099: the configuration as a JSON document, planned and applied in the portal;
        # the discovery document of a new or moved identity provider is fetched through the guard.
        app.state.configuration_documents = document_service(
            uow,
            clock,
            app.state.admin_service,
            settings=live,
            discovery=discovery_fetcher or GuardedDiscoveryFetcher(http, allow_private=lab),
            locked=app.state.live_settings.locked,
        )
        # ADR 0099 point 14: a SQLite package (ADR 0089) is still read for one release,
        # converted to a document and applied by the service above.
        app.state.configuration_packages = ConfigurationPackageService(
            SqliteConfigurationTransfer(
                database_path(settings.database_url), secret_box(settings), now=clock.now
            ),
            app.state.configuration_documents,
            clock,
            app.state.admin_service,
            max_bytes=lambda: live().configuration_package_max_mb * 1024 * 1024,
        )
        app.state.reviewed_documents = ReviewedDocuments()
        app.state.certificate_status = CertificateStatus(uow)
        # ADR 0086: the round trip to the public address, the endpoint it asks and the watch on
        # forwarded headers from a peer that is not trusted.
        app.state.address_status = AddressStatus(uow)
        app.state.instance_service = InstanceService(uow, clock)
        try:
            await app.state.instance_service.prepare()
        except Exception as exc:  # e.g. schema not migrated yet; the endpoint then answers 500
            structlog.get_logger().warning("instance.prepare_failed", error=type(exc).__name__)
        app.state.system_log = SystemLog(uow, clock)
        app.state.proxy_watch = ProxyHeaderWatch(
            clock, lambda: timedelta(hours=live().tls_alert_repeat_hours)
        )
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
                certificate_probe or internal_ca.certificate_probe(settings),
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
        # ADR 0117 point 9: a team member's usage facts, only while the organisation has them on.
        staff_usage = StaffUsage(
            [
                k
                for k in (agent_pseudonym_key(settings), agent_pseudonym_key_previous(settings))
                if k is not None
            ],
            lambda organisation: per_organisation(organisation).reporting_staff_view_enabled,
        )
        app.state.queue_service = QueueService(
            uow,
            clock,
            app.state.admin_service,
            agent_pseudonym_key(settings),
            agent_pseudonym_key_previous(settings),
            staff_usage,
            request_work(clock, app.state.live_settings),
        )
        # ADR 0121 step 7: immediate work on a pool, for participants and for the own queue.
        keys = (agent_pseudonym_key(settings), agent_pseudonym_key_previous(settings))
        app.state.immediate_requests, app.state.request_queue = request_services(
            uow, clock, app.state.admin_service, app.state.live_settings, keys
        )

        async def own_connection(organisation_id: int) -> bool:
            """Whether the organisation has its own contact-center connection (ADR 0032)."""
            has_own = own_connection_of(app.state.contact_center_connections)
            return has_own is not None and await has_own(organisation_id)

        app.state.integration_service = IntegrationService(
            uow,
            clock,
            app.state.admin_service,
            settings.app_version,
            probes=ProbeReports(ReportStore(app.state.engine)),
            staff_usage=staff_usage,
            # ADR 0121 step 6b: task events move the contact-center attempts of request work.
            work=AttemptEvents(
                request_work(clock, app.state.live_settings),
                lambda: live().contact_center_reconcile_minutes,
                own_connection,
            ),
        )
        # ADR 0082 points 5 and 7: the last inbound webhooks and the health of the connections.
        app.state.health_service = IntegrationHealthService(
            uow, clock, lambda: live().health_policy()
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
        # ADR 0044 §2: the participant's contact details on the booking page, on request.
        app.state.participant_contact_service = ParticipantContactService(
            uow, clock, app.state.admin_service
        )
        app.state.branding_service = BrandingService(uow, clock, app.state.admin_service)
        app.state.contact_service = ContactService(uow, clock, app.state.admin_service)
        app.state.readiness_service = ReadinessService(uow, clock, app.state.admin_service)
        app.state.overview_layout_service = OverviewLayoutService(
            uow, clock, [w.key for w in WIDGETS], lambda: live().overview_max_widgets
        )
        app.state.scenario_service = ScenarioService(uow, clock, app.state.admin_service)
        app.state.embedding_service = EmbeddingService(
            uow,
            clock,
            app.state.admin_service,
            max_origins=lambda: live().embedding_max_origins,
            allow_local=settings.environment != "prod",
        )
        app.state.holiday_service = HolidayService(uow, clock, app.state.admin_service)
        app.state.skill_service = SkillService(
            uow, clock, app.state.admin_service, lambda: live().max_skills_per_organisation
        )
        app.state.capacity_service = CapacityService(
            uow,
            clock,
            app.state.admin_service,
            lambda: live().max_capacity_pools_per_organisation,
        )
        app.state.pool_planning_service = PoolPlanningService(
            uow,
            clock,
            app.state.admin_service,
            lambda org: per_organisation(org).shortfall_horizon_days,
        )
        app.state.flow_service = FlowService(
            uow, clock, app.state.admin_service, lambda: live().max_flows_per_service
        )
        app.state.agenda_service = AgendaService(
            uow,
            clock,
            app.state.admin_service,
            lambda org: per_organisation(org).shortfall_horizon_days,
        )
        # ADR 0061 §7: the open alerts of an admin, read for the banner on every admin page.
        app.state.alert_service = AlertService(
            uow,
            clock,
            lambda: read_heartbeat(live().worker_heartbeat_path),
            lambda: timedelta(seconds=live().worker_heartbeat_max_age_seconds),
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
            txt_resolver,
            lambda: live().mail_from if live().smtp_host else None,
            production=settings.environment == "prod",
            plaintext_relay_allowed=settings.smtp_plaintext_relay_allowed,
        )
        # ADR 0082 point 9: alerts to email addresses and Webex spaces, and the bot tokens.
        app.state.bot_tokens = alert_bots(app.state.engine, settings)
        # ADR 0032 point 9: each organisation's own contact-center connection, sealed.
        app.state.contact_center_connections = sealed_connections(app.state.engine, settings)
        app.state.contact_center_connection_service = ContactCenterConnectionService(
            uow,
            clock,
            app.state.admin_service,
            app.state.contact_center_connections,
            lambda: app.state.integrations.organisations,
        )
        app.state.alert_target_service = AlertTargetService(
            uow,
            clock,
            app.state.admin_service,
            ConfiguredAlertSenders(
                senders,
                lambda: fallback_sender(live()),
                app.state.bot_tokens,
                http,
                live().webex_api_base_url,
                request_log,
            ),
            alert_composer(
                app.state.translator.gettext,
                settings.public_base_url,
                settings.app_version,
                settings.admin_timezone,
            ),
            app.state.bot_tokens,
        )
        # ADR 0082 point 11: a group admin ends a fallback by hand; the worker switches it.
        app.state.fallback_service = FallbackService(
            uow,
            clock,
            app.state.admin_service,
            app.state.integration_service,
            BotSpacePoster(app.state.bot_tokens, http, live().webex_api_base_url, request_log),
            SpaceTexts(app.state.translator.gettext, settings.public_base_url),
        )
        # ADR 0121 step 6b: contact-center work the platform failed goes back with a notice.
        app.state.integration_service.set_requeue_handler(
            app.state.fallback_service.dispatch_failed
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
            uow,
            clock,
            app.state.admin_service,
            app.state.booking_service,
            app.state.live_settings.closure_retention,
        )
        app.state.planner_service = planner_service(
            uow,
            clock,
            app.state.admin_service,
            app.state.booking_service,
            app.state.live_settings,
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
        app.state.usage_api_service = UsageApiService(
            uow,
            app.state.api_access,
            clock,
            lambda: live().reporting_min_cell_size,
            lambda organisation: per_organisation(organisation).reporting_org_min_cell_size,
            lambda: UsageApiLimits(
                live().reporting_api_page_size, live().reporting_export_max_days
            ),
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
                "configuration_packages",  # ADR 0089 point 7: the same service as the portal
                "configuration_documents",  # ADR 0099: the document service, same as the portal
                "usage_api_service",  # ADR 0117 point 13: the hourly figures, scoped to the client
                "change_request_service",  # ADR 0023 step 2
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
            set_line_tap(None)
            shipping_stop.set()
            await shipping
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
    # Admin session (ADR 0098 point 12): signed, HttpOnly, SameSite=Lax; `__Host-op_admin`,
    # `Path=/` and `Secure` when PUBLIC_BASE_URL is https, whatever ENVIRONMENT says.
    cookie = session_cookie_for(settings.public_base_url)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret(settings),
        session_cookie=cookie.name,
        path=cookie.path,
        max_age=settings.admin_session_hours * 3600,
        same_site="lax",
        https_only=cookie.secure,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    # ADR 0086: forwarded headers from a peer that FORWARDED_ALLOW_IPS does not name.
    app.add_middleware(ProxyHeaderMiddleware)
    # ADR 0086 point 4: a redirect to the request's own origin goes to the public address.
    app.add_middleware(PublicRedirectMiddleware)
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
        display_router,
        health.router,
        instance.router,
        pages.router,
        booking_pages.router,
        join_pages.router,
        call_pages.router,
        trial_pages.router,
        direct_pages.router,
        immediate_pages.router,
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
