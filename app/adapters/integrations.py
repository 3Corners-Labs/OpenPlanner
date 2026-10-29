"""Build the integration adapters from the settings (one place for web and worker).

Without a configured Service App the Webex adapters are ``None`` and OpenPlanner runs without
video meetings and work items; bookings keep working. Without a settings key there is no
``SecretBox``, so channel secrets (ADR 0022) can be neither stored nor read.
"""

from dataclasses import dataclass

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.crypto import SecretBox
from app.adapters.db.credentials import CredentialStore
from app.adapters.group_check import CheckerConfig, PlatformChecker
from app.adapters.webex_auth import AuthorizedClient, ServiceAppConfig, WebexTokens
from app.adapters.webex_meetings import WebexMeetings
from app.adapters.wxcc import WxccTasks
from app.config import Settings
from app.domain.ports import (
    Clock,
    ContactCenterDispatcher,
    IntegrationChecker,
    VideoProvider,
)
from app.domain.security_log import SecurityLog
from app.keys import settings_encryption_key

log = structlog.get_logger()


@dataclass(frozen=True)
class Integrations:
    """The configured integration adapters (``None`` when not configured)."""

    tokens: WebexTokens | None = None
    video: VideoProvider | None = None
    dispatcher: ContactCenterDispatcher | None = None
    checker: IntegrationChecker | None = None


def secret_box(settings: Settings) -> SecretBox | None:
    """Return the box for integration secrets, or ``None`` when no settings key is available.

    In production that means ``SETTINGS_ENCRYPTION_KEY`` is unset; organisations can then only
    use channels without a secret (and the environment's SMTP fallback).
    """
    try:
        return SecretBox(settings_encryption_key(settings))
    except RuntimeError:
        log.warning("settings_key.missing")
        return None


def build_integrations(
    settings: Settings,
    engine: AsyncEngine,
    http: httpx.AsyncClient,
    clock: Clock,
    security: SecurityLog | None = None,
) -> Integrations:
    """Return the adapters the settings allow; ``security`` records failing token refreshes."""
    client_id, secret, refresh = (
        settings.webex_client_id,
        settings.webex_client_secret,
        settings.webex_refresh_token,
    )
    if not client_id or secret is None or refresh is None:
        return Integrations()
    config = ServiceAppConfig(
        client_id=client_id,
        client_secret=secret.get_secret_value(),
        bootstrap_refresh_token=refresh.get_secret_value(),
        oauth_url=f"{settings.webex_api_base_url.rstrip('/')}/access_token",
    )
    store = CredentialStore(engine, SecretBox(settings_encryption_key(settings)))
    tokens = WebexTokens(config, http, store, clock, security)
    client = AuthorizedClient(http, tokens)
    video = WebexMeetings(client, settings.webex_api_base_url)
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
    )
    return Integrations(tokens, video, dispatcher, checker)
