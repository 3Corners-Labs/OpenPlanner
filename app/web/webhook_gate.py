"""The one gate every inbound webhook passes (ADR 0124).

A route under ``/webhooks/`` declares what it is, through a resolver that finds its secret, and
depends on a ``WebhookGate``; it gets a verified, parsed ``Delivery`` or the call is refused
before the route runs. No route verifies anything itself, and a test fails for a ``/webhooks/``
route without the gate.

    call ─► address exists ─► rate limit ─► content type ─► size ─► signature ─► time ─► shape
              404               429           415            413      401         401      400

Every refusal answers with an empty body and the status of its reason
(``app.domain.inbound_gate``), and is reported through ``app.web.security_events.report`` with
the reason, the platform and, for an organisation's address, the organisation's slug. The slug
of an organisation that does not exist is never written: it comes from the caller. The replay
check stays with the domain's event key (ADR 0124 point 7 is a later step). The caller's address
is the one the server derived (``--proxy-headers``); nothing is read from a forwarded header
here.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn

import structlog
from fastapi import Request
from slowapi.util import get_remote_address
from starlette.responses import Response

from app.adapters.contact_center_connections import SealedConnections
from app.adapters.contact_centers import inbound
from app.adapters.webhooks import InboundWebhook, check_webex
from app.domain.admin import AdminService
from app.domain.inbound_gate import MAX_BODY_BYTES, Refusal, accepts_content_type
from app.domain.ports import Clock
from app.domain.scheduling import is_slug
from app.domain.security_log import WEBHOOK_REJECTED
from app.web.protection import within_unknown_address_limit, within_webhook_limit
from app.web.request_body import read_capped
from app.web.security_events import report

log = structlog.get_logger()

RETRY_AFTER_SECONDS = "60"
"""What a rate-limited webhook is told: the limits count per minute."""
UNKNOWN_SOURCE = "unknown"
"""The source code written when the platform itself is not known (the code is fixed, never the
caller's text)."""
INSTALLATION = "-"
"""The organisation part of the rate-limit bucket of an address that belongs to no organisation."""

FLOOD_REASONS = frozenset({Refusal.RATE, Refusal.UNKNOWN_ADDRESS})
"""Reasons that a flood produces by definition: no log line per call, the security event (held
back when repeated) is their trace."""

Verifier = Callable[[bytes, Mapping[str, str], str, datetime], Refusal | None]
"""``(body, headers, secret, now)`` → ``None`` when the call is authentic and recent."""


@dataclass(frozen=True)
class Address:
    """An address that exists: who it is for and the secret its calls are verified with.

    Attributes:
        source: The platform's code for logs, events, limits and replay keys (``webex``, ``wxcc``).
        secret: The secret the signature is checked under.
        verify: The platform's check of signature and signed time.
        handling: The contact-center platform's handling, ``None`` for the video platform.
        organisation_id: The organisation whose own connection this is; ``None`` for an
            address of the installation.
        organisation: That organisation's slug, public and no personal data.
    """

    source: str
    secret: str
    verify: Verifier
    handling: InboundWebhook | None = None
    organisation_id: int | None = None
    organisation: str | None = None


@dataclass(frozen=True)
class Unresolved:
    """An address that does not exist, or has no secret; the reason is always the same 404.

    Attributes:
        source: The platform's code, or ``UNKNOWN_SOURCE`` when even that is not known.
        organisation: The slug of an organisation that does exist but has no connection; never
            one that does not exist.
    """

    source: str
    organisation: str | None = None


Resolver = Callable[[Request], Awaitable[Address | Unresolved]]
"""Finds the address a request is for, from the application's state and the path."""


@dataclass(frozen=True)
class Delivery:
    """A webhook that passed the gate: authentic, recent, small and a JSON object.

    Attributes:
        address: The address it came in at.
        payload: The body as a JSON object.
        now: The time the gate used, for the route to reuse.
    """

    address: Address
    payload: dict[str, Any]
    now: datetime


class WebhookRefused(Exception):  # noqa: N818 - reads as an outcome, like HTTPException
    """The gate refused a call; the application answers it with an empty body.

    Args:
        reason: Why, from the closed list.
    """

    def __init__(self, reason: Refusal) -> None:
        """Store the reason."""
        super().__init__(reason.value)
        self.reason = reason


async def webhook_refused_response(request: Request, exc: Exception) -> Response:
    """Answer a refused webhook: the status of its reason, no body, ``Retry-After`` for a rate."""
    reason = exc.reason if isinstance(exc, WebhookRefused) else Refusal.MALFORMED
    headers = {"retry-after": RETRY_AFTER_SECONDS} if reason is Refusal.RATE else None
    return Response(status_code=reason.status, headers=headers)


async def _refuse(
    request: Request, reason: Refusal, source: str, organisation: str | None = None
) -> NoReturn:
    """Leave the trace of a refusal, then refuse.

    The event carries the reason, the platform's code and the organisation's slug, and the
    caller's pseudonym; never the body, a header, the signature or the address. A failure to
    write it is logged by ``report`` and does not change the answer.
    """
    if reason not in FLOOD_REASONS:
        log.warning("webhook.rejected", source=source, reason=reason.value)
    detail = f"source:{source}" + (f",organisation:{organisation}" if organisation else "")
    await report(request, WEBHOOK_REJECTED, reason.value, detail=detail)
    raise WebhookRefused(reason)


def _json_object(body: bytes) -> dict[str, Any] | None:
    """Return the body as a JSON object, or ``None`` if it is not one."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


class WebhookGate:
    """FastAPI dependency: admit a webhook call or refuse it, in the fixed order of ADR 0124.

    Args:
        resolve: Finds the address the request is for; the route's declaration of what it is.
    """

    def __init__(self, resolve: Resolver) -> None:
        """Store the resolver."""
        self._resolve = resolve

    async def __call__(self, request: Request) -> Delivery:
        """Return the verified delivery, or raise ``WebhookRefused`` after leaving its trace."""
        caller = get_remote_address(request)
        found = await self._resolve(request)
        if isinstance(found, Unresolved):
            if not within_unknown_address_limit(caller):
                await _refuse(request, Refusal.RATE, UNKNOWN_SOURCE)
            await _refuse(request, Refusal.UNKNOWN_ADDRESS, found.source, found.organisation)
        address = found
        who = (address.source, address.organisation)
        # Seam: the source network (ADR 0124 point 3) is checked here, before the limit.
        bucket = address.organisation or INSTALLATION
        if not within_webhook_limit(address.source, bucket, caller):
            await _refuse(request, Refusal.RATE, *who)
        if not accepts_content_type(request.headers.get("content-type")):
            await _refuse(request, Refusal.CONTENT_TYPE, *who)
        body = await read_capped(request, MAX_BODY_BYTES)
        if body is None:
            await _refuse(request, Refusal.TOO_LARGE, *who)
        clock: Clock = request.app.state.clock
        now = clock.now()
        failure = address.verify(body, request.headers, address.secret, now)
        if failure is not None:
            await _refuse(request, failure, *who)
        payload = _json_object(body)
        if payload is None:
            await _refuse(request, Refusal.MALFORMED, *who)
        return Delivery(address, payload, now)


async def webex_address(request: Request) -> Address | Unresolved:
    """Resolve the video platform's address; without ``WEBEX_WEBHOOK_SECRET`` it does not exist."""
    secret = request.app.state.settings.webex_webhook_secret
    if secret is None:
        return Unresolved("webex")
    return Address("webex", secret.get_secret_value(), check_webex)


def installation_address(fixed: InboundWebhook | None = None) -> Resolver:
    """Return the resolver of a contact-center address that uses the installation's secret.

    Args:
        fixed: The platform of an address that names it in its path (the alias ``/webhooks/wxcc``);
            ``None`` reads the platform from the ``{kind}`` in the path. A platform without an
            adapter, or without its secret set, does not exist (ADR 0032 point 6).
    """

    async def resolve(request: Request) -> Address | Unresolved:
        handling = fixed or inbound(str(request.path_params.get("kind", "")))
        if handling is None:
            return Unresolved(UNKNOWN_SOURCE)
        secret = handling.secret(request.app.state.settings)
        if secret is None:
            return Unresolved(handling.source)
        return Address(handling.source, secret.get_secret_value(), handling.verify, handling)

    return resolve


async def organisation_address(request: Request) -> Address | Unresolved:
    """Resolve an organisation's own contact-center address (ADR 0032 point 10).

    It exists when the platform has an adapter, the organisation exists and is connected to that
    platform with a secret; the secret is that organisation's own.
    """
    handling = inbound(str(request.path_params.get("kind", "")))
    if handling is None:
        return Unresolved(UNKNOWN_SOURCE)
    slug = str(request.path_params.get("org", ""))
    connections: SealedConnections | None = request.app.state.contact_center_connections
    if connections is None or not is_slug(slug):
        return Unresolved(handling.source)
    admin: AdminService = request.app.state.admin_service
    organisation_id = await admin.organisation_id(slug)
    if organisation_id is None:
        return Unresolved(handling.source)
    secret = await connections.webhook_secret(organisation_id, handling.kind)
    if secret is None:
        return Unresolved(handling.source, slug)
    return Address(handling.source, secret, handling.verify, handling, organisation_id, slug)
