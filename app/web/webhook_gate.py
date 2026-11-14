"""The one gate every inbound webhook passes (ADR 0124).

A route under ``/webhooks/`` depends on a gate made by ``webhook_gate(resolver)``. The resolver is
a FastAPI dependency that declares the path parameters it needs (``kind``, ``org``) and finds the
address and its secret; the gate then admits the call and hands the route a verified, parsed
``Delivery``, or refuses it before the route runs. No route verifies anything itself, and a test
fails for a ``/webhooks/`` route without a gate (``is_webhook_gate``).

    call ─► address exists ─► source network ─► content type ─► size ─► signature ─► time ─► shape
              404 / 429         403              415            413      401         401      400
          ─► the limits ─► paused?
              429            204, the event is dropped

The limits come last for an address that exists, so a call that cannot sign never uses up the
budget of the authentic ones (ADR 0124 point 5): a call refused for any reason before them counts
against a bucket of refused calls (over it, the reason becomes ``rate``), and only a call that
passed everything counts against the bucket of verified calls. A call to an address that does not
exist counts against a bucket of its own, per caller address; it never touches an address that
exists.

Every refusal answers with an empty body and the status of its reason
(``app.domain.inbound_gate``), and is reported through ``app.web.security_events.report`` with
the reason, the platform and, for an organisation's address, the organisation's slug. The slug
of an organisation that does not exist is never written: it comes from the caller. The source
network (``app.domain.webhook_sources``, point 3 and 4) is checked first: a list that applies and a
caller outside it is ``network``, one whose address cannot be trusted (decided for this call,
never from a standing state) is ``source_unknown``; both answer 403 and count as refused calls.
The replay check is the domain's (``app.domain.webhook_replay``, ADR 0124 point 7): the gate hands
the route the identity of the verified body for an event without an identifier of its own. A
paused address (point 9) passes every check above, signature included, and only then is its
verified event dropped: the answer is 204, the route is not called and nothing is remembered, so a
redelivery after resuming is handled; a call that would be refused is refused as for any address.
The caller's address is the one the server derived (``--proxy-headers``), counted by its /64 when
it is IPv6 (``caller_of``); nothing is read from a forwarded header here.

The delivery ledger (``app.domain.webhook_ledger``, point 10) is told at the places an outcome is
decided and decides nothing itself: ``_refuse`` for every refusal (with whether the call came from
inside the connection's own list of networks and the per-call fault of the proxy chain), the pause
for a verified event it drops, and ``app.api.webhooks`` for what came of an accepted event
(accepted, a duplicate, unknown work). Telling it, or writing its alert, never changes the answer.
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
from app.config import Settings
from app.domain.contact_center_connection import ContactCenterConnectionService
from app.domain.inbound_gate import MAX_BODY_BYTES, Refusal, accepts_content_type
from app.domain.networks import NetworkList
from app.domain.ports import Clock
from app.domain.security_log import WEBHOOK_REJECTED
from app.domain.webhook_ledger import AddressKey, Delivered
from app.domain.webhook_replay import body_identity
from app.domain.webhook_sources import ChainFault, judge_source, source_list_that_applies
from app.web.caller import caller_of, client_address
from app.web.protection import (
    webhook_retry_after,
    within_refused_limit,
    within_unknown_address_limit,
    within_webhook_limit,
)
from app.web.request_body import read_capped
from app.web.security_events import report
from app.web.webhook_ledger import note_delivered, note_refused
from app.web.webhook_sources import contact_center_networks, proxy_chain, video_networks

log = structlog.get_logger()

UNKNOWN_SOURCE = "unknown"
"""The source code written when the platform itself is not known (the code is fixed, never the
caller's text)."""
INSTALLATION = "-"
"""The organisation part of the rate-limit bucket of an address that belongs to no organisation."""

FLOOD_REASONS = frozenset(
    {Refusal.RATE, Refusal.UNKNOWN_ADDRESS, Refusal.NETWORK, Refusal.SOURCE_UNKNOWN}
)
"""Reasons that a passer-by produces by definition (a call to a made-up address, over a limit, from
outside the list, or whose address cannot be trusted): no log line per call, the security event
(held back when repeated) is their trace."""


@dataclass(frozen=True)
class Address[W: InboundWebhook]:
    """An address that exists: its platform's record and the secret its calls are verified with.

    Attributes:
        inbound: How the platform's webhooks are checked and read (source, verify, parse, ...).
        secret: The secret the signature is checked under.
        organisation_id: The organisation whose own connection this is; ``None`` for an
            address of the installation.
        organisation: That organisation's slug, public and no personal data.
        sources: The list of allowed source networks that governs this address (the
            connection's own, else the installation's default for the platform); empty: none.
        paused: Whether the connection is paused (ADR 0124 point 9): verified events are dropped.
            An installation-wide address has no connection and is never paused.
    """

    inbound: W
    secret: str
    organisation_id: int | None = None
    organisation: str | None = None
    sources: NetworkList = NetworkList()
    paused: bool = False


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
        body_identity: The identity of the verified body, for an event without an identifier
            of its own (``app.domain.webhook_replay``, ADR 0124 point 7).
    """

    address: Address[W]
    payload: dict[str, Any]
    now: datetime
    body_identity: str


class WebhookRefused(Exception):  # noqa: N818 - reads as an outcome, like HTTPException
    """The gate refused a call; the application answers it with an empty body.

    Args:
        reason: Why, from the closed list.
    """

    def __init__(self, reason: Refusal) -> None:
        """Store the reason."""
        super().__init__(reason.value)
        self.reason = reason


class WebhookPaused(Exception):  # noqa: N818 - reads as an outcome, like HTTPException
    """A verified event for a paused address; the application answers 204 and drops it."""


async def webhook_paused_response(request: Request, exc: Exception) -> Response:
    """Answer a verified event of a paused address: 204, so the platform keeps its subscription."""
    return Response(status_code=204)


async def webhook_refused_response(request: Request, exc: Exception) -> Response:
    """Answer a refused webhook: the status of its reason, no body, ``Retry-After`` for a rate."""
    reason = exc.reason if isinstance(exc, WebhookRefused) else Refusal.MALFORMED
    headers = {"retry-after": webhook_retry_after()} if reason is Refusal.RATE else None
    return Response(status_code=reason.status, headers=headers)


def key_of(address: Address[InboundWebhook] | Unresolved) -> AddressKey:
    """Return the ledger's key of an address: the platform and the organisation's slug.

    An address that does not exist is counted under its platform only, never under a slug the
    caller made up (ADR 0124 point 10).
    """
    if isinstance(address, Unresolved):
        return AddressKey(address.source, unknown=True)
    return AddressKey(address.inbound.source, address.organisation)


async def _refuse(
    request: Request,
    reason: Refusal,
    address: Address[InboundWebhook] | Unresolved,
    *,
    inside_list: bool = False,
    fault: ChainFault | None = None,
) -> NoReturn:
    """Leave the trace of a refusal, then refuse: the one place a refusal is decided.

    The event carries the reason, the platform's code and the organisation's slug, and the
    caller's pseudonym; never the body, a header, the signature or the address. The delivery
    ledger is told the same, with whether the call came from inside the connection's own list of
    networks and the per-call fault of the proxy chain. A failure to write either is logged and
    does not change the answer.

    Args:
        request: The refused call.
        reason: Why, from the closed list.
        address: The address it was for.
        inside_list: A list applies and the call passed its source check.
        fault: The fault of this call's proxy chain behind ``source_unknown``.
    """
    source = address.source if isinstance(address, Unresolved) else address.inbound.source
    organisation = address.organisation
    if reason not in FLOOD_REASONS:
        log.warning("webhook.rejected", source=source, reason=reason.value)
    detail = f"source:{source}" + (f",organisation:{organisation}" if organisation else "")
    await report(request, WEBHOOK_REJECTED, reason.value, detail=detail)
    await note_refused(request, key_of(address), reason, inside_list=inside_list, fault=fault)
    raise WebhookRefused(reason)


@dataclass(frozen=True)
class _Failed:
    """The first check a call failed.

    Attributes:
        reason: Why it is refused.
        inside_list: A list applies and the call passed its source check, so it came from inside.
        fault: The fault of this call's proxy chain, for ``source_unknown``.
    """

    reason: Refusal
    inside_list: bool = False
    fault: ChainFault | None = None


def _json_object(body: bytes) -> dict[str, Any] | None:
    """Return the body as a JSON object, or ``None`` if it is not one."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


async def _inspect(
    request: Request, address: Address[InboundWebhook]
) -> _Failed | tuple[dict[str, Any], datetime, str]:
    """Run the checks of an address that exists, up to the shape; no limit is counted here.

    The source network comes first (ADR 0124 point 3): a caller from outside the list, or whose
    address cannot be trusted while a list applies, is not read at all. Both refusals answer
    alike, and no forwarded header is read here: the address is the one the server derived.

    Returns:
        The first check that fails (its reason, whether the call came from inside the list and
        the fault of its proxy chain), else the payload, the time used and the identity of the
        verified body.
    """
    settings: Settings = request.app.state.settings
    source, fault = judge_source(
        client_address(request),
        address.sources,
        required=settings.webhook_source_networks_required,
        chain=proxy_chain(request),
    )
    if source is not None:
        return _Failed(source, fault=fault)
    # The source check passed: with a list that applies, the call is from inside it.
    inside = bool(address.sources.networks)
    if not accepts_content_type(request.headers.get("content-type")):
        return _Failed(Refusal.CONTENT_TYPE, inside)
    body = await read_capped(request, MAX_BODY_BYTES)
    if body is None:
        return _Failed(Refusal.TOO_LARGE, inside)
    clock: Clock = request.app.state.clock
    now = clock.now()
    failure = address.inbound.verify(body, request.headers, address.secret, now)
    if failure is not None:
        return _Failed(failure, inside)
    payload = _json_object(body)
    if payload is None:
        return _Failed(Refusal.MALFORMED, inside)
    return payload, now, body_identity(body)


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
                await _refuse(request, Refusal.RATE, found)
            await _refuse(request, Refusal.UNKNOWN_ADDRESS, found)
        address = found
        source, organisation = address.inbound.source, address.organisation
        bucket = organisation or INSTALLATION
        inspected = await _inspect(request, address)
        if isinstance(inspected, _Failed):
            if not within_refused_limit(source, bucket, caller):
                await _refuse(request, Refusal.RATE, address, inside_list=inspected.inside_list)
            await _refuse(
                request,
                inspected.reason,
                address,
                inside_list=inspected.inside_list,
                fault=inspected.fault,
            )
        if not within_webhook_limit(source, bucket, caller):
            await _refuse(
                request, Refusal.RATE, address, inside_list=bool(address.sources.networks)
            )
        payload, now, identity = inspected
        if address.paused:
            # Verified, so a stranger cannot get a 204 this way; dropped before any handling.
            log.info("webhook.paused", source=source, organisation=organisation)
            await note_delivered(request, key_of(address), Delivered.PAUSED)
            raise WebhookPaused
        return Delivery(address, payload, now, identity)

    setattr(gate, GATE_MARKER, True)
    return gate


async def webex_address(request: Request) -> Address[InboundWebhook] | Unresolved:
    """Resolve the video platform's address; without ``WEBEX_WEBHOOK_SECRET`` it does not exist."""
    secret = WEBEX_INBOUND.secret(request.app.state.settings)
    if secret is None:
        return Unresolved(WEBEX_INBOUND.source)
    return Address(
        WEBEX_INBOUND,
        secret.get_secret_value(),
        sources=video_networks(request),
    )


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
    return Address(
        handling,
        secret.get_secret_value(),
        sources=contact_center_networks(request),
    )


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
    sources = source_list_that_applies(target.allowed_networks, contact_center_networks(request))
    return Address(
        handling,
        target.secret,
        target.organisation_id,
        target.organisation,
        sources,
        paused=not target.inbound_enabled,
    )
