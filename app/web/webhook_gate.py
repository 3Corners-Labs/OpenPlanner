"""The one gate every inbound webhook passes (ADR 0124).

A route under ``/webhooks/`` depends on a gate made by ``webhook_gate(resolver)``. The resolver is
a FastAPI dependency that declares the path parameters it needs (``kind``, ``org``) and finds the
address and its secret; the gate then admits the call and hands the route a verified, parsed
``Delivery``, or refuses it before the route runs. No route verifies anything itself, and a test
fails for a ``/webhooks/`` route without a gate (``is_webhook_gate``).

    call ─► address exists ─► content type ─► size ─► signature ─► time ─► shape ─► the limits
              404 / 429         415            413      401         401      400       429

The limits come last for an address that exists, so a call that cannot sign never uses up the
budget of the authentic ones (ADR 0124 point 5): a call refused for any reason before them counts
against a bucket of refused calls (over it, the reason becomes ``rate``), and only a call that
passed everything counts against the bucket of verified calls. A call to an address that does not
exist counts against a bucket of its own, per caller address; it never touches an address that
exists.

Every refusal answers with an empty body and the status of its reason
(``app.domain.inbound_gate``), and is reported through ``app.web.security_events.report`` with
the reason, the platform and, for an organisation's address, the organisation's slug. The slug
of an organisation that does not exist is never written: it comes from the caller. The replay
check stays with the domain's event key (ADR 0124 point 7 is a later step). The caller's address
is the one the server derived (``--proxy-headers``), counted by its /64 when it is IPv6
(``caller_of``); nothing is read from a forwarded header here.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, NoReturn

import structlog
from fastapi import Depends, Request
from starlette.responses import Response

from app.adapters.contact_centers import inbound
from app.adapters.webhooks import (
    WEBEX_INBOUND,
    WXCC_INBOUND,
    ContactCenterWebhook,
    InboundWebhook,
)
from app.domain.contact_center_connection import ContactCenterConnectionService
from app.domain.inbound_gate import MAX_BODY_BYTES, Refusal, accepts_content_type
from app.domain.ports import Clock
from app.domain.security_log import WEBHOOK_REJECTED
from app.web.caller import caller_of
from app.web.protection import (
    webhook_retry_after,
    within_refused_limit,
    within_unknown_address_limit,
    within_webhook_limit,
)
from app.web.request_body import read_capped
from app.web.security_events import report

log = structlog.get_logger()

UNKNOWN_SOURCE = "unknown"
"""The source code written when the platform itself is not known (the code is fixed, never the
caller's text)."""
INSTALLATION = "-"
"""The organisation part of the rate-limit bucket of an address that belongs to no organisation."""

FLOOD_REASONS = frozenset({Refusal.RATE, Refusal.UNKNOWN_ADDRESS})
"""Reasons that a flood produces by definition: no log line per call, the security event (held
back when repeated) is their trace."""


@dataclass(frozen=True)
class Address[W: InboundWebhook]:
    """An address that exists: its platform's record and the secret its calls are verified with.

    Attributes:
        inbound: How the platform's webhooks are checked and read (source, verify, parse, ...).
        secret: The secret the signature is checked under.
        organisation_id: The organisation whose own connection this is; ``None`` for an
            address of the installation.
        organisation: That organisation's slug, public and no personal data.
    """

    inbound: W
    secret: str
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


@dataclass(frozen=True)
class Delivery[W: InboundWebhook]:
    """A webhook that passed the gate: authentic, recent, small and a JSON object.

    Attributes:
        address: The address it came in at.
        payload: The body as a JSON object.
        now: The time the gate used, for the route to reuse.
    """

    address: Address[W]
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
    headers = {"retry-after": webhook_retry_after()} if reason is Refusal.RATE else None
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


async def _inspect(
    request: Request, address: Address[InboundWebhook]
) -> Refusal | tuple[dict[str, Any], datetime]:
    """Run the checks of an address that exists, up to the shape; no limit is counted here.

    Returns:
        The reason of the first check that fails, else the payload and the time used.
    """
    if not accepts_content_type(request.headers.get("content-type")):
        return Refusal.CONTENT_TYPE
    body = await read_capped(request, MAX_BODY_BYTES)
    if body is None:
        return Refusal.TOO_LARGE
    clock: Clock = request.app.state.clock
    now = clock.now()
    failure = address.inbound.verify(body, request.headers, address.secret, now)
    if failure is not None:
        return failure
    payload = _json_object(body)
    if payload is None:
        return Refusal.MALFORMED
    return payload, now


GATE_MARKER = "is_webhook_gate"
"""The attribute ``webhook_gate`` sets on the dependency it returns; the route sweep reads it."""


def is_webhook_gate(call: object) -> bool:
    """Return whether ``call`` was made by ``webhook_gate``: what the route sweep looks for."""
    return getattr(call, GATE_MARKER, False) is True


def webhook_gate[W: InboundWebhook](
    resolve: Callable[..., Awaitable[Address[W] | Unresolved]],
) -> Callable[..., Awaitable[Delivery[W]]]:
    """Return the FastAPI dependency that admits or refuses a webhook call (ADR 0124 point 2).

    Args:
        resolve: A dependency that finds the address the request is for. It declares the path
            parameters it needs itself, so the route's parameters are honest.
    """

    async def gate(
        request: Request, found: Annotated[Address[W] | Unresolved, Depends(resolve)]
    ) -> Delivery[W]:
        """Return the verified delivery, or raise ``WebhookRefused`` after leaving its trace."""
        caller = caller_of(request)
        if isinstance(found, Unresolved):
            # Only an address that does not exist counts here: it never touches one that does.
            if not within_unknown_address_limit(caller):
                await _refuse(request, Refusal.RATE, found.source)
            await _refuse(request, Refusal.UNKNOWN_ADDRESS, found.source, found.organisation)
        address = found
        source, organisation = address.inbound.source, address.organisation
        bucket = organisation or INSTALLATION
        # Seam: the source network (ADR 0124 point 3) is checked here, before the limits.
        inspected = await _inspect(request, address)
        if isinstance(inspected, Refusal):
            if not within_refused_limit(source, bucket, caller):
                await _refuse(request, Refusal.RATE, source, organisation)
            await _refuse(request, inspected, source, organisation)
        if not within_webhook_limit(source, bucket, caller):
            await _refuse(request, Refusal.RATE, source, organisation)
        payload, now = inspected
        return Delivery(address, payload, now)

    setattr(gate, GATE_MARKER, True)
    return gate


async def webex_address(request: Request) -> Address[InboundWebhook] | Unresolved:
    """Resolve the video platform's address; without ``WEBEX_WEBHOOK_SECRET`` it does not exist."""
    secret = WEBEX_INBOUND.secret(request.app.state.settings)
    if secret is None:
        return Unresolved(WEBEX_INBOUND.source)
    return Address(WEBEX_INBOUND, secret.get_secret_value())


def _installation(
    request: Request, handling: ContactCenterWebhook | None
) -> Address[ContactCenterWebhook] | Unresolved:
    """Resolve a contact-center address that uses the installation's secret (ADR 0032 point 11).

    A platform without an adapter, or without its secret set, does not exist (point 6).
    """
    if handling is None:
        return Unresolved(UNKNOWN_SOURCE)
    secret = handling.secret(request.app.state.settings)
    if secret is None:
        return Unresolved(handling.source)
    return Address(handling, secret.get_secret_value())


async def installation_address(
    request: Request, kind: str
) -> Address[ContactCenterWebhook] | Unresolved:
    """Resolve ``/webhooks/contact-center/{kind}``."""
    return _installation(request, inbound(kind))


async def alias_address(request: Request) -> Address[ContactCenterWebhook] | Unresolved:
    """Resolve the alias of Webex Contact Center's address (ADR 0032 CC-4)."""
    return _installation(request, WXCC_INBOUND)


async def organisation_address(
    request: Request, kind: str, org: str
) -> Address[ContactCenterWebhook] | Unresolved:
    """Resolve ``/webhooks/contact-center/{kind}/{org}``: an organisation's own address.

    The rule of when it exists is the connection service's (``webhook_target``); this only reads
    the path and calls it.
    """
    handling = inbound(kind)
    if handling is None:
        return Unresolved(UNKNOWN_SOURCE)
    service: ContactCenterConnectionService = request.app.state.contact_center_connection_service
    target = await service.webhook_target(org, handling.kind)
    if not target.found or target.secret is None:
        return Unresolved(handling.source, target.organisation)
    return Address(handling, target.secret, target.organisation_id, target.organisation)
