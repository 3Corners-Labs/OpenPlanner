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
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
import structlog

from app.domain.audit import Outcome
from app.domain.ports import REASON_LIMIT, Clock, IntegrationError, RequestLog, RequestRecord
from app.domain.security_log import TOKEN_REFRESH_FAILED, SecurityLog

log = structlog.get_logger()

CREDENTIAL_NAME = "webex_service_app"
REFRESH_MARGIN = timedelta(minutes=5)
EXPIRY_WARNING = timedelta(days=14)
ADDRESS = re.compile(r"[^\s@'\"]+@[^\s@'\"]+")
"""An email address in Webex's error text (the group's host); masked before logging."""


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
        security: Records a failing refresh as a security event (ADR 0034); ``None``: log only.
    """

    def __init__(
        self,
        config: ServiceAppConfig,
        http: httpx.AsyncClient,
        store: TokenStore,
        clock: Clock,
        security: SecurityLog | None = None,
    ) -> None:
        """Store the collaborators."""
        self._security = security
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

    async def _report(self, reason: str, detail: str) -> None:
        """Record a failed refresh as a security event; a failure here is only logged."""
        if self._security is None:
            return
        try:
            await self._security.record(
                TOKEN_REFRESH_FAILED, Outcome.FAILURE, reason=reason, detail=detail
            )
        except Exception as exc:  # the refresh error itself is what the caller must see
            log.warning("security_log.failed", error=type(exc).__name__)

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
            await self._report("unreachable", type(exc).__name__)
            raise IntegrationError("unreachable", retryable=True) from exc
        if response.status_code in (400, 401):
            log.error("webex.token_refresh_refused", status=response.status_code)
            await self._report("unauthorized", f"status:{response.status_code}")
            raise IntegrationError("unauthorized", retryable=False)
        if response.status_code >= 300:
            log.warning("webex.token_refresh_failed", status=response.status_code)
            await self._report("unreachable", f"status:{response.status_code}")
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
    put in errors: they can contain personal data. Every request is reported to the
    ``RequestLog`` (ADR 0068) as method, path, status, tracking id and duration: never a body,
    a query string or a header. A failing report is logged and never fails the request.

    Args:
        http: Shared HTTP client.
        tokens: Source of access tokens.
        request_log: Where every request is reported; ``None`` reports nothing.
    """

    def __init__(
        self, http: httpx.AsyncClient, tokens: WebexTokens, request_log: RequestLog | None = None
    ) -> None:
        """Store the collaborators."""
        self._http = http
        self._tokens = tokens
        self._request_log = request_log

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
        carry no participant data, since Webex may echo submitted values.

        Raises:
            IntegrationError: ``unauthorized``, ``rejected`` (4xx; not retryable),
                ``rate_limited``, ``server_error`` or ``unreachable`` (retryable).
        """
        path = httpx.URL(url).path
        for attempt in (0, 1):
            token = await self._tokens.access_token(refresh=attempt == 1)
            started = time.monotonic()
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
                await self._report(RequestRecord(method, path, 0, "unreachable", "", _ms(started)))
                raise IntegrationError("unreachable", retryable=True) from exc
            if response.status_code == 401 and attempt == 0:
                await self._report(_record(method, path, response, "unauthorized", started))
                continue
            if response.status_code in ok:
                await self._report(_record(method, path, response, "", started))
                return response
            failure = http_failure(response, explain=explain)
            await self._report(_record(method, path, response, failure.code, started))
            raise failure
        raise IntegrationError("unauthorized", retryable=False)  # pragma: no cover - loop ends

    async def _report(self, record: RequestRecord) -> None:
        """Hand the record to the request log; a failure there never fails the request."""
        if self._request_log is None:
            return
        try:
            await self._request_log.record(record)
        except Exception as exc:  # the request itself is what the caller must see
            log.warning("request_log.failed", error=type(exc).__name__)


def _ms(started: float) -> int:
    """Return the milliseconds since ``started`` (a ``time.monotonic`` value)."""
    return int((time.monotonic() - started) * 1000)


def _record(
    method: str, path: str, response: httpx.Response, error: str, started: float
) -> RequestRecord:
    """Describe a finished request: the tracking id from the header or, failing that, the body."""
    tracking_id = response.headers.get("trackingid", "") or _refusal(response)[1]
    return RequestRecord(method, path, response.status_code, error, tracking_id, _ms(started))


def http_failure(response: httpx.Response, *, explain: bool = False) -> IntegrationError:
    """Map an unexpected status to an ``IntegrationError`` and log why Webex refused.

    Shared by every HTTP adapter (Webex Meetings, WxCC, Webex Connect SMS, GitHub releases).
    The log line carries the status and the ``trackingId``, if any; with ``explain``
    (requests without participant data only) also Webex's reason with email addresses masked,
    which the error then carries to the timeline and the system event (ADR 0065). Never the
    rest of the body.
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
        return IntegrationError("unauthorized", retryable=False, reason=reason)
    if status == 429:
        return IntegrationError("rate_limited", retryable=True, reason=reason)
    if status >= 500:
        return IntegrationError("server_error", retryable=True, reason=reason)
    return IntegrationError(f"rejected_{status}", retryable=False, reason=reason)


def _refusal(response: httpx.Response) -> tuple[str, str]:
    """Return Webex's reason (addresses masked, shortened) and tracking id, if any.

    Webex puts the specific reason in ``errors[].description`` and a generic sentence in
    ``message`` ("malformed syntax, see 'errors'"); the descriptions win when there are any.
    """
    try:
        body = response.json()
    except ValueError:
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    message, tracking_id, errors = body.get("message"), body.get("trackingId"), body.get("errors")
    descriptions = [
        error["description"]
        for error in (errors if isinstance(errors, list) else [])
        if isinstance(error, dict) and isinstance(error.get("description"), str)
    ]
    text = "; ".join(descriptions) if descriptions else message if isinstance(message, str) else ""
    reason = ADDRESS.sub("***", text)[:REASON_LIMIT]
    return reason, tracking_id if isinstance(tracking_id, str) else ""
