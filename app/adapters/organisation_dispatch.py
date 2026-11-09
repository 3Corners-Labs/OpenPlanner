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
from app.adapters.db.record_names import organisation_tokens_name
from app.adapters.webex_auth import ServiceAppConfig
from app.adapters.wxcc import WxccTasks, wxcc_tasks
from app.domain.contact_center import ContactCenterKind
from app.domain.ports import Clock, ContactCenterDispatcher, OwnConnection, RequestLog
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
    ) -> None:
        """Store the collaborators; no dispatcher is built yet."""
        self._connections = connections
        self._tokens = tokens
        self._http = http
        self._clock = clock
        self._oauth_url = oauth_url
        self._security = security
        self._request_log = request_log
        self._built: dict[int, tuple[str, WxccTasks]] = {}

    async def own(self, organisation_id: int, kind: ContactCenterKind) -> OwnConnection | None:
        """Return the organisation's own connection, its WxCC dispatcher built or reused.

        ``None`` without a connection; a dispatcher of ``None`` when it cannot dispatch ``kind``.
        """
        credentials = await self._connections.credentials(organisation_id)
        if credentials is None:
            self._built.pop(organisation_id, None)
            return None
        return OwnConnection(self._dispatcher(organisation_id, credentials, kind))

    def _dispatcher(
        self, organisation_id: int, credentials: ConnectionCredentials, kind: ContactCenterKind
    ) -> ContactCenterDispatcher | None:
        """Return the organisation's WxCC dispatcher, or ``None`` when it cannot dispatch."""
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
            return built[1]
        config = ServiceAppConfig(
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            bootstrap_refresh_token=credentials.refresh_token,
            oauth_url=self._oauth_url,
        )
        _, dispatcher = wxcc_tasks(
            config,
            self._http,
            self._tokens,
            self._clock,
            credentials.api_base_url,
            security=self._security,
            request_log=self._request_log,
            name=organisation_tokens_name(organisation_id),
        )
        self._built[organisation_id] = (digest, dispatcher)
        return dispatcher
