"""Access tokens of the Webex Service App (ADR 0017/0018): refresh, rotate, store encrypted.

The first refresh uses the bootstrap refresh token from the environment
(``WEBEX_REFRESH_TOKEN``); after that the rotated tokens live encrypted in the database
(``CredentialStore``), shared by web and worker. The stored record keeps a fingerprint of the
bootstrap token: putting a new one in the environment (after renewing the tokens or adding
scopes) makes OpenPlanner start from it instead of the stored tokens. A token is refreshed
five minutes before it expires, or at once after the API answered 401.

    access_token() ─► stored and valid? ─► use it
                   └► POST /access_token (grant_type=refresh_token) ─► store ─► use it
"""

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
import structlog

from app.domain.ports import Clock, IntegrationError

log = structlog.get_logger()

CREDENTIAL_NAME = "webex_service_app"
REFRESH_MARGIN = timedelta(minutes=5)
EXPIRY_WARNING = timedelta(days=14)
ADDRESS = re.compile(r"[^\s@'\"]+@[^\s@'\"]+")
"""An email address in Webex's error text (the group's host); masked before logging."""
REASON_LIMIT = 300


class TokenStore(Protocol):
    """Where tokens are kept (``CredentialStore``; a fake in tests)."""

    async def get(self, name: str) -> dict[str, Any] | None:
        """Return the stored value, if any."""
        ...

    async def put(self, name: str, value: dict[str, Any], now: datetime) -> None:
        """Store the value."""
        ...


@dataclass(frozen=True)
class ServiceAppConfig:
    """Registration of the Service App at Webex."""

    client_id: str
    client_secret: str = field(repr=False)
    bootstrap_refresh_token: str = field(repr=False)
    oauth_url: str = "https://webexapis.com/v1/access_token"


@dataclass(frozen=True)
class TokenHealth:
    """What the admin portal shows about the Service App's tokens (no token values)."""

    configured: bool
    access_expires_at: datetime | None
    refresh_expires_at: datetime | None
    refresh_expiring_soon: bool


class WebexTokens:
    """Hands out a valid access token.

    Args:
        config: The Service App registration.
        http: Shared HTTP client.
        store: Encrypted token storage.
        clock: Current time.
    """

    def __init__(
        self, config: ServiceAppConfig, http: httpx.AsyncClient, store: TokenStore, clock: Clock
    ) -> None:
        """Store the collaborators."""
        self._config = config
        self._http = http
        self._store = store
        self._clock = clock
        self._lock = asyncio.Lock()
        # Identifies the bootstrap token without storing it (first 16 hex of SHA-256).
        bootstrap = config.bootstrap_refresh_token.encode()
        self._fingerprint = hashlib.sha256(bootstrap).hexdigest()[:16]

    async def access_token(self, *, refresh: bool = False) -> str:
        """Return a valid access token, refreshing it when needed or when ``refresh`` is set.

        Raises:
            IntegrationError: ``unauthorized`` (not retryable) when Webex refuses the refresh
                token; ``unreachable`` (retryable) on network or server errors.
        """
        async with self._lock:
            now = self._clock.now()
            stored = await self._store.get(CREDENTIAL_NAME)
            if stored and stored.get("bootstrap") != self._fingerprint:
                stored = None  # a new bootstrap token in the environment wins
            if (
                stored
                and not refresh
                and _expires(stored, "access_expires_at") - now > REFRESH_MARGIN
            ):
                return str(stored["access_token"])
            refresh_token = (
                stored["refresh_token"] if stored else self._config.bootstrap_refresh_token
            )
            tokens = await self._refresh(str(refresh_token), now)
            tokens["bootstrap"] = self._fingerprint
            await self._store.put(CREDENTIAL_NAME, tokens, now)
            if _expires(tokens, "refresh_expires_at") - now < EXPIRY_WARNING:
                log.warning("webex.refresh_token_expiring")
            return str(tokens["access_token"])

    async def health(self) -> TokenHealth:
        """Return when the tokens expire, for the admin portal."""
        stored = await self._store.get(CREDENTIAL_NAME)
        if not stored:
            return TokenHealth(True, None, None, False)
        refresh_at = _expires(stored, "refresh_expires_at")
        return TokenHealth(
            True,
            _expires(stored, "access_expires_at"),
            refresh_at,
            refresh_at - self._clock.now() < EXPIRY_WARNING,
        )

    async def _refresh(self, refresh_token: str, now: datetime) -> dict[str, Any]:
        """Exchange the refresh token for new tokens."""
        try:
            response = await self._http.post(
                self._config.oauth_url,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "refresh_token": refresh_token,
                },
            )
        except httpx.HTTPError as exc:
            log.warning("webex.token_refresh_failed", error=type(exc).__name__)
            raise IntegrationError("unreachable", retryable=True) from exc
        if response.status_code in (400, 401):
            log.error("webex.token_refresh_refused", status=response.status_code)
            raise IntegrationError("unauthorized", retryable=False)
        if response.status_code >= 300:
            log.warning("webex.token_refresh_failed", status=response.status_code)
            raise IntegrationError("unreachable", retryable=True)
        body = response.json()
        return {
            "access_token": body["access_token"],
            "access_expires_at": (now + timedelta(seconds=int(body["expires_in"]))).isoformat(),
            "refresh_token": body.get("refresh_token", refresh_token),
            "refresh_expires_at": (
                now + timedelta(seconds=int(body.get("refresh_token_expires_in", 0)))
            ).isoformat(),
        }


def _expires(value: dict[str, Any], key: str) -> datetime:
    """Return the stored expiry ``key`` as a datetime."""
    return datetime.fromisoformat(str(value[key]))


class AuthorizedClient:
    """Calls Webex and WxCC APIs with the Service App's token; maps failures to codes.

    A 401 refreshes the token once and repeats the call. Response bodies are never logged or
    put in errors: they can contain personal data.

    Args:
        http: Shared HTTP client.
        tokens: Source of access tokens.
    """

    def __init__(self, http: httpx.AsyncClient, tokens: WebexTokens) -> None:
        """Store the collaborators."""
        self._http = http
        self._tokens = tokens

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        ok: tuple[int, ...] = (200, 201, 204),
        explain: bool = False,
    ) -> httpx.Response:
        """Send the request and return the response if its status is in ``ok``.

        ``explain`` logs Webex's error message on a refusal; pass it only for requests that
        carry no citizen data, since Webex may echo submitted values.

        Raises:
            IntegrationError: ``unauthorized``, ``rejected`` (4xx; not retryable),
                ``rate_limited``, ``server_error`` or ``unreachable`` (retryable).
        """
        for attempt in (0, 1):
            token = await self._tokens.access_token(refresh=attempt == 1)
            try:
                response = await self._http.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                log.warning("integration.unreachable", error=type(exc).__name__)
                raise IntegrationError("unreachable", retryable=True) from exc
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code in ok:
                return response
            raise http_failure(response, explain=explain)
        raise IntegrationError("unauthorized", retryable=False)  # pragma: no cover - loop ends


def http_failure(response: httpx.Response, *, explain: bool = False) -> IntegrationError:
    """Map an unexpected status to an ``IntegrationError`` and log why Webex refused.

    Shared by every Webex API adapter (Meetings, WxCC, Connect SMS). The log line carries the
    status and Webex's ``trackingId`` for Webex support; with ``explain`` (requests without
    citizen data only) also Webex's ``message`` with email addresses masked. Never the rest
    of the body.
    """
    status = response.status_code
    reason, tracking_id = _refusal(response)
    reason = reason if explain else ""
    log.warning(
        "integration.refused",
        status=status,
        path=response.request.url.path,
        reason=reason,
        tracking_id=tracking_id,
    )
    if status == 401:
        return IntegrationError("unauthorized", retryable=False)
    if status == 429:
        return IntegrationError("rate_limited", retryable=True)
    if status >= 500:
        return IntegrationError("server_error", retryable=True)
    return IntegrationError(f"rejected_{status}", retryable=False)


def _refusal(response: httpx.Response) -> tuple[str, str]:
    """Return Webex's error message (addresses masked, shortened) and tracking id, if any."""
    try:
        body = response.json()
    except ValueError:
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    message, tracking_id = body.get("message"), body.get("trackingId")
    reason = ADDRESS.sub("***", message)[:REASON_LIMIT] if isinstance(message, str) else ""
    return reason, tracking_id if isinstance(tracking_id, str) else ""
