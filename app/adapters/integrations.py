"""Build the integration adapters from the settings (one place for web and worker).

Without a configured Service App the Webex adapters are ``None`` and OpenPlanner runs without
video meetings and work items; bookings keep working. Without a settings key there is no
``SecretBox``, so channel secrets (ADR 0022) can be neither stored nor read.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.contact_center_connections import SealedConnections
from app.adapters.crypto import SecretBox
from app.adapters.db.credentials import CredentialStore
from app.adapters.db.probes import ProbeReports
from app.adapters.db.reports import ReportStore
from app.adapters.group_check import CheckerConfig, PlatformChecker
from app.adapters.organisation_dispatch import OrganisationWxcc
from app.adapters.webex_auth import AuthorizedClient, ServiceAppConfig, WebexTokens
from app.adapters.webex_meetings import WebexMeetings
from app.adapters.wxcc import WxccTasks
from app.config import Settings
from app.domain.contact_center import ContactCenterKind
from app.domain.ports import (
    Clock,
    ContactCenterDispatchers,
    IntegrationChecker,
    OrganisationDispatchers,
    RequestLog,
    VideoProvider,
)
from app.domain.security_log import SecurityLog
from app.keys import settings_encryption_key, settings_encryption_key_previous

log = structlog.get_logger()


@dataclass(frozen=True)
class Integrations:
    """The configured integration adapters (``None`` or empty when not configured).

    ``dispatchers`` holds the installation's dispatcher per contact-center platform (ADR 0032
    point 5); ``organisations`` builds each organisation's own (point 9), ``None`` without a
    settings key.
    """

    tokens: WebexTokens | None = None
    video: VideoProvider | None = None
    dispatchers: ContactCenterDispatchers = field(default_factory=dict)
    checker: IntegrationChecker | None = None
    organisations: OrganisationDispatchers | None = None


def webex_fingerprint(settings: Settings) -> str:
    """Return a digest of the settings ``build_integrations`` uses, to notice a change.

    Web and worker rebuild their Webex and WxCC adapters when it changes (ADR 0042 step 6b);
    a digest, so no second copy of the secrets is kept.
    """
    parts = (
        settings.webex_client_id or "",
        settings.webex_client_secret.get_secret_value() if settings.webex_client_secret else "",
        settings.webex_refresh_token.get_secret_value() if settings.webex_refresh_token else "",
        settings.wxcc_api_base_url or "",
        settings.webex_api_base_url,
    )
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def secret_box(settings: Settings) -> SecretBox | None:
    """Return the box for integration secrets, or ``None`` when no settings key is available.

    In production that means ``SETTINGS_ENCRYPTION_KEY`` is unset; organisations can then only
    use channels without a secret (and the environment's SMTP fallback).
    """
    try:
        return installation_box(settings)
    except RuntimeError:
        log.warning("settings_key.missing")
        return None


def sealed_store(engine: AsyncEngine, settings: Settings) -> CredentialStore | None:
    """Return the encrypted credential store, or ``None`` without a settings key.

    The one home of "sealed per-organisation records need a key": the alert bot tokens and the
    contact-center connections both start from it.
    """
    box = secret_box(settings)
    return CredentialStore(engine, box) if box is not None else None


def sealed_connections(engine: AsyncEngine, settings: Settings) -> SealedConnections | None:
    """Return the organisations' contact-center connections, or ``None`` without a key."""
    store = sealed_store(engine, settings)
    return SealedConnections(store) if store is not None else None


def installation_box(settings: Settings) -> SecretBox:
    """Return the box under the current ``SETTINGS_ENCRYPTION_KEY``, reading the previous one too.

    Raises:
        RuntimeError: If the current key is missing in production or either key is too short.
    """
    previous = settings_encryption_key_previous(settings)
    return SecretBox(settings_encryption_key(settings), [previous] if previous else [])


def build_integrations(
    settings: Settings,
    engine: AsyncEngine,
    http: httpx.AsyncClient,
    clock: Clock,
    security: SecurityLog | None = None,
    request_log: RequestLog | None = None,
    probe_timeout: Callable[[], timedelta] | None = None,
) -> Integrations:
    """Return the adapters the settings allow.

    ``security`` records failing token refreshes; ``request_log`` receives every request the
    adapters make (ADR 0068); ``probe_timeout`` is how long the group check waits for its
    probe work item's events (ADR 0082; the settings' value when not given).
    """
    client_id, secret, refresh = (
        settings.webex_client_id,
        settings.webex_client_secret,
        settings.webex_refresh_token,
    )
    organisations = _organisation_dispatchers(settings, engine, http, clock, security, request_log)
    if not client_id or secret is None or refresh is None:
        return Integrations(organisations=organisations)
    config = ServiceAppConfig(
        client_id=client_id,
        client_secret=secret.get_secret_value(),
        bootstrap_refresh_token=refresh.get_secret_value(),
        oauth_url=f"{settings.webex_api_base_url.rstrip('/')}/access_token",
    )
    store = CredentialStore(engine, installation_box(settings))
    tokens = WebexTokens(config, http, store, clock, security)
    client = AuthorizedClient(http, tokens, request_log)
    video = WebexMeetings(client, settings.webex_api_base_url, clock)
    dispatcher = (
        WxccTasks(client, settings.wxcc_api_base_url, clock)
        if settings.dispatch_configured and settings.wxcc_api_base_url
        else None
    )
    webex_secret, wxcc_secret = settings.webex_webhook_secret, settings.wxcc_webhook_secret
    checker = PlatformChecker(
        http,
        tokens,
        CheckerConfig(
            webex_base_url=settings.webex_api_base_url,
            wxcc_base_url=settings.wxcc_api_base_url if dispatcher else None,
            webhook_base=settings.webhook_base,
            webex_secret=webex_secret.get_secret_value() if webex_secret else None,
            wxcc_secret=wxcc_secret.get_secret_value() if wxcc_secret else None,
        ),
        clock,
        request_log,
        dispatcher=dispatcher,
        probes=ProbeReports(ReportStore(engine)) if dispatcher else None,
        probe_timeout=probe_timeout
        or (lambda: timedelta(seconds=settings.integration_probe_timeout_seconds)),
    )
    dispatchers: ContactCenterDispatchers = (
        {ContactCenterKind.WEBEX_CC: dispatcher} if dispatcher else {}
    )
    return Integrations(tokens, video, dispatchers, checker, organisations)


def _organisation_dispatchers(
    settings: Settings,
    engine: AsyncEngine,
    http: httpx.AsyncClient,
    clock: Clock,
    security: SecurityLog | None,
    request_log: RequestLog | None,
) -> OrganisationDispatchers | None:
    """Return the organisations' own dispatchers, or ``None`` without a settings key."""
    store = sealed_store(engine, settings)
    if store is None:
        return None
    oauth_url = f"{settings.webex_api_base_url.rstrip('/')}/access_token"
    return OrganisationWxcc(
        SealedConnections(store), store, http, clock, oauth_url, security, request_log
    )
