"""Each organisation's own WxCC dispatcher, from its sealed connection (ADR 0032 point 9).

An organisation with a connection of its own gets a ``WxccTasks`` on its own API host, signed
in with its own Service App: its client and refresh token, its access tokens kept under
``organisation_tokens_name``. The dispatcher is built on first use and rebuilt when the
connection changes (a digest of its fields, never a second copy of the secrets).

A connection of another kind, or one without both OAuth secrets, gives no dispatcher: the
organisation's tasks then wait for it and never go to the installation's tenant
(``DispatcherChoice``).
"""

import hashlib

import httpx

from app.adapters.contact_center_connections import ConnectionCredentials, SealedConnections
from app.adapters.db.credentials import Secrets
from app.adapters.db.uow import organisation_tokens_name
from app.adapters.webex_auth import AuthorizedClient, ServiceAppConfig, WebexTokens
from app.adapters.webhook_registration import replace_wxcc
from app.adapters.webhooks import WXCC_INBOUND
from app.adapters.wxcc import WxccTasks, wxcc_tasks
from app.domain.contact_center import ContactCenterKind, Registration
from app.domain.ports import (
    Clock,
    ContactCenterDispatcher,
    IntegrationError,
    OwnConnection,
    RequestLog,
)
from app.domain.security_log import SecurityLog


def _digest(credentials: ConnectionCredentials) -> str:
    """Return a digest of the connection, to notice a change without keeping the secrets."""
    parts = (
        credentials.api_base_url,
        credentials.client_id,
        credentials.client_secret,
        credentials.refresh_token,
    )
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


class OrganisationWxcc:
    """``OrganisationDispatchers`` for Webex Contact Center.

    Args:
        connections: The sealed connections.
        tokens: Where each organisation's access tokens are kept (``CredentialStore``).
        http: Shared HTTP client.
        clock: Current time.
        oauth_url: The Webex token endpoint.
        security: Records a failing token refresh (ADR 0034).
        request_log: Receives every request (ADR 0068).
        webhook_base: Where the platforms call back (``Settings.webhook_base``), for the
            organisation's webhook address (CC-4d).
    """

    def __init__(
        self,
        connections: SealedConnections,
        tokens: Secrets,
        http: httpx.AsyncClient,
        clock: Clock,
        oauth_url: str,
        security: SecurityLog | None = None,
        request_log: RequestLog | None = None,
        webhook_base: str = "",
    ) -> None:
        """Store the collaborators; no dispatcher is built yet."""
        self._webhook_base = webhook_base.rstrip("/")
        self._connections = connections
        self._tokens = tokens
        self._http = http
        self._clock = clock
        self._oauth_url = oauth_url
        self._security = security
        self._request_log = request_log
        self._built: dict[int, tuple[str, WebexTokens, WxccTasks]] = {}

    async def own(self, organisation_id: int, kind: ContactCenterKind) -> OwnConnection | None:
        """Return the organisation's own connection, its WxCC dispatcher built or reused.

        ``None`` without a connection; a dispatcher of ``None`` when it cannot dispatch ``kind``.
        """
        credentials = await self._connections.credentials(organisation_id)
        if credentials is None:
            self._built.pop(organisation_id, None)
            return None
        return OwnConnection(self._dispatcher(organisation_id, credentials, kind))

    async def register(self, organisation_id: int, organisation: str) -> Registration:
        """Make WxCC send the organisation's events to its own address, with its secret (CC-4d).

        The subscriptions at the address are replaced, so a rotated secret takes effect.
        """
        credentials = await self._connections.credentials(organisation_id)
        secret = await self._connections.webhook_secret(organisation_id, ContactCenterKind.WEBEX_CC)
        if credentials is None or secret is None:
            return Registration.NOT_READY
        built = self._build(organisation_id, credentials, ContactCenterKind.WEBEX_CC)
        if built is None:
            return Registration.NOT_READY
        tokens, _ = built
        client = AuthorizedClient(self._http, tokens, self._request_log)
        target = f"{self._webhook_base}{WXCC_INBOUND.organisation_path(organisation)}"
        try:
            line = await replace_wxcc(client, credentials.api_base_url, target, secret)
        except IntegrationError as error:
            return Registration.FAILED if error.retryable else Registration.REFUSED
        return Registration.REGISTERED if line.endswith("created") else Registration.REFUSED

    def _dispatcher(
        self, organisation_id: int, credentials: ConnectionCredentials, kind: ContactCenterKind
    ) -> ContactCenterDispatcher | None:
        """Return the organisation's WxCC dispatcher, or ``None`` when it cannot dispatch."""
        built = self._build(organisation_id, credentials, kind)
        return built[1] if built is not None else None

    def _build(
        self, organisation_id: int, credentials: ConnectionCredentials, kind: ContactCenterKind
    ) -> tuple[WebexTokens, WxccTasks] | None:
        """Return the organisation's tokens and dispatcher, built or reused; ``None`` if none."""
        if (
            credentials.kind is not ContactCenterKind.WEBEX_CC
            or kind is not ContactCenterKind.WEBEX_CC
            or not credentials.complete
        ):
            self._built.pop(organisation_id, None)
            return None
        digest = _digest(credentials)
        built = self._built.get(organisation_id)
        if built is not None and built[0] == digest:
            return built[1], built[2]
        config = ServiceAppConfig(
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            bootstrap_refresh_token=credentials.refresh_token,
            oauth_url=self._oauth_url,
        )
        tokens, dispatcher = wxcc_tasks(
            config,
            self._http,
            self._tokens,
            self._clock,
            credentials.api_base_url,
            security=self._security,
            request_log=self._request_log,
            name=organisation_tokens_name(organisation_id),
        )
        self._built[organisation_id] = (digest, tokens, dispatcher)
        return tokens, dispatcher
