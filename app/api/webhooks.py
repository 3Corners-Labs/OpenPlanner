"""Inbound webhooks from Webex and the contact-center platforms (ADR 0010, 0017, 0018, 0032).

Authenticated by signature, not by an admin session. Every route here is a declaration: it
depends on a ``WebhookGate`` (``app.web.webhook_gate``, ADR 0124) that checks the address, the
rate, the content type, the size, the signature and the time, and hands the route a verified
``Delivery``; a route verifies nothing itself, and a test fails for one without the gate. The
secret is configured per installation (``WEBEX_WEBHOOK_SECRET``, ``WXCC_WEBHOOK_SECRET``) or per
organisation's connection; without it the endpoint does not exist (404). A contact-center
platform's check and parsing come from its adapter (``contact_centers.INBOUND``); a kind without
an adapter is a 404 too. Unknown meetings, tasks and event types, and events seen before
(replays), are acknowledged and ignored, so the platforms do not retry. Every verified webhook is
noted as its platform's latest inbound event, for *Koppelingen* and *System status* (ADR 0082
point 5).

    POST /webhooks/webex                   meetings started/ended, participants joined/left
    POST /webhooks/contact-center/{kind}/{org}  an organisation's own connection (ADR 0032)
    POST /webhooks/contact-center/{kind}   task created/connected/ended/failed
    POST /webhooks/wxcc                    alias of /webhooks/contact-center/webex_cc
"""

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.adapters.webhooks import (
    CONTACT_CENTER_PATH,
    WEBEX_PATH,
    WXCC_INBOUND,
    ContactCenterWebhook,
    InboundWebhook,
)
from app.domain.connection_health import InboundEvent, Platform
from app.domain.integration import EventOutcome, IntegrationService
from app.domain.integration_health import IntegrationHealthService
from app.domain.ports import Clock
from app.web.webhook_gate import (
    Delivery,
    alias_address,
    installation_address,
    organisation_address,
    webex_address,
    webhook_gate,
)

router = APIRouter(include_in_schema=False)
log = structlog.get_logger()

WebexDelivery = Annotated[Delivery[InboundWebhook], Depends(webhook_gate(webex_address))]
InstallationDelivery = Annotated[
    Delivery[ContactCenterWebhook], Depends(webhook_gate(installation_address))
]
AliasDelivery = Annotated[Delivery[ContactCenterWebhook], Depends(webhook_gate(alias_address))]
OrganisationDelivery = Annotated[
    Delivery[ContactCenterWebhook], Depends(webhook_gate(organisation_address))
]


def _service(request: Request) -> IntegrationService:
    """Return the application's integration service."""
    service: IntegrationService = request.app.state.integration_service
    return service


@router.post(WEBEX_PATH)
async def webex(request: Request, delivery: WebexDelivery) -> Response:
    """Record a Webex meeting or participant event."""
    inbound = delivery.address.inbound
    event = inbound.parse(delivery.payload)
    outcome = EventOutcome.IGNORED
    if event is not None:
        outcome = await _service(request).record_meeting_event(
            event.target_id, event.kind, delivery.now, event_key=event.event_key
        )
        log.info(
            "webhook.webex",
            kind=event.kind.value,
            outcome=outcome.value,
            meeting_id=event.target_id.split("_I_", 1)[0],
        )
    await _note(request, inbound, delivery.payload, outcome)
    return Response(status_code=204)


@router.post(CONTACT_CENTER_PATH + "/{kind}")
async def contact_center(request: Request, delivery: InstallationDelivery) -> Response:
    """Record a task event of the contact-center platform in the path (ADR 0032 point 6).

    A kind without an adapter is a 404, like a platform without a secret.
    """
    return await _record_task_event(request, delivery)


@router.post(WXCC_INBOUND.registered_path)
async def wxcc(request: Request, delivery: AliasDelivery) -> Response:
    """Record a WxCC task event: the alias of ``/contact-center/webex_cc`` (ADR 0032 point 6).

    Kept until the WxCC subscriptions are registered at the new address (ADR 0032 CC-4).
    """
    return await _record_task_event(request, delivery)


@router.post(CONTACT_CENTER_PATH + "/{kind}/{org}")
async def organisation_contact_center(request: Request, delivery: OrganisationDelivery) -> Response:
    """Record a task event of an organisation's own connection (ADR 0032 point 10).

    Verified with that organisation's webhook secret; the task is looked up in its bookings
    only. An unknown kind or organisation, or one without that connection, is a 404.
    """
    return await _record_task_event(request, delivery)


async def _record_task_event(
    request: Request, delivery: Delivery[ContactCenterWebhook]
) -> Response:
    """Parse and record one verified contact-center webhook with its platform's handling.

    The organisation of the address limits the task lookup to that organisation's bookings;
    an address of the installation still finds a task of any organisation.
    """
    inbound = delivery.address.inbound
    event = inbound.parse(delivery.payload)
    outcome = EventOutcome.IGNORED
    if event is not None:
        outcome = await _service(request).record_task_event(
            event.target_id,
            event.kind,
            delivery.now,
            event.detail,
            event_key=event.event_key,
            origin=event.origin,
            provider=inbound.kind,
            source=inbound.source,
            organisation_id=delivery.address.organisation_id,
        )
        log.info(
            "webhook.contact_center",
            platform=inbound.kind.value,
            kind=event.kind.value,
            outcome=outcome.value,
        )
    await _note(request, inbound, delivery.payload, outcome)
    return Response(status_code=204)


async def _note(
    request: Request, inbound: InboundWebhook, payload: dict[str, Any], outcome: EventOutcome
) -> None:
    """Note a verified webhook as its platform's latest (ADR 0082 point 5); never fails it."""
    health: IntegrationHealthService = request.app.state.health_service
    clock: Clock = request.app.state.clock
    platform: Platform = inbound.platform
    try:
        await health.note_inbound(
            InboundEvent(platform, clock.now(), inbound.event_type(payload), outcome.value)
        )
    except Exception as exc:  # the event is recorded; its note must not turn into a 500
        log.warning("webhook.note_failed", source=platform.value, error=type(exc).__name__)
