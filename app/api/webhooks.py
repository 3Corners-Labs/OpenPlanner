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

from app.adapters.contact_centers import INBOUND
from app.adapters.webhooks import parse_webex
from app.domain.connection_health import InboundEvent, Platform
from app.domain.contact_center import ContactCenterKind
from app.domain.integration import EventOutcome, IntegrationService
from app.domain.integration_health import IntegrationHealthService
from app.domain.ports import Clock
from app.web.webhook_gate import (
    Delivery,
    WebhookGate,
    installation_address,
    organisation_address,
    webex_address,
)

router = APIRouter(prefix="/webhooks", include_in_schema=False)
log = structlog.get_logger()

_webex_gate = WebhookGate(webex_address)
_installation_gate = WebhookGate(installation_address())
_alias_gate = WebhookGate(installation_address(INBOUND[ContactCenterKind.WEBEX_CC]))
_organisation_gate = WebhookGate(organisation_address)

WebexDelivery = Annotated[Delivery, Depends(_webex_gate)]
InstallationDelivery = Annotated[Delivery, Depends(_installation_gate)]
AliasDelivery = Annotated[Delivery, Depends(_alias_gate)]
OrganisationDelivery = Annotated[Delivery, Depends(_organisation_gate)]


def _service(request: Request) -> IntegrationService:
    """Return the application's integration service."""
    service: IntegrationService = request.app.state.integration_service
    return service


@router.post("/webex")
async def webex(request: Request, delivery: WebexDelivery) -> Response:
    """Record a Webex meeting or participant event."""
    payload = delivery.payload
    event = parse_webex(payload)
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
    await _note(request, Platform.WEBEX, _webex_kind(payload), outcome)
    return Response(status_code=204)


@router.post("/contact-center/{kind}")
async def contact_center(request: Request, delivery: InstallationDelivery) -> Response:
    """Record a task event of the contact-center platform in the path (ADR 0032 point 6).

    A kind without an adapter is a 404, like a platform without a secret.
    """
    return await _record_task_event(request, delivery)


@router.post("/wxcc")
async def wxcc(request: Request, delivery: AliasDelivery) -> Response:
    """Record a WxCC task event: the alias of ``/contact-center/webex_cc`` (ADR 0032 point 6).

    Kept until the WxCC subscriptions are registered at the new address (ADR 0032 CC-4).
    """
    return await _record_task_event(request, delivery)


@router.post("/contact-center/{kind}/{org}")
async def organisation_contact_center(
    request: Request,
    kind: str,
    org: str,
    delivery: OrganisationDelivery,
) -> Response:
    """Record a task event of an organisation's own connection (ADR 0032 point 10).

    Verified with that organisation's webhook secret; the task is looked up in its bookings
    only. An unknown kind or organisation, or one without that connection, is a 404. The path
    parameters are declared here although the gate reads them itself: a route that addresses an
    organisation says so in its signature, which is what the authorisation sweep looks at.
    """
    return await _record_task_event(request, delivery)


async def _record_task_event(request: Request, delivery: Delivery) -> Response:
    """Parse and record one verified contact-center webhook with its platform's handling.

    The organisation of the address limits the task lookup to that organisation's bookings;
    an address of the installation still finds a task of any organisation.
    """
    handling = delivery.address.handling
    if handling is None:  # pragma: no cover - every contact-center address carries its platform
        return Response(status_code=204)
    payload = delivery.payload
    event = handling.parse(payload)
    outcome = EventOutcome.IGNORED
    if event is not None:
        outcome = await _service(request).record_task_event(
            event.target_id,
            event.kind,
            delivery.now,
            event.detail,
            event_key=event.event_key,
            origin=event.origin,
            provider=handling.kind,
            source=handling.source,
            organisation_id=delivery.address.organisation_id,
        )
        log.info(
            "webhook.contact_center",
            platform=handling.kind.value,
            kind=event.kind.value,
            outcome=outcome.value,
        )
    await _note(request, handling.platform, handling.event_type(payload), outcome)
    return Response(status_code=204)


def _webex_kind(payload: dict[str, Any]) -> str:
    """Return a Webex event's type as ``resource:event`` (codes only)."""
    return f"{payload.get('resource', '')}:{payload.get('event', '')}"[:64]


async def _note(request: Request, platform: Platform, kind: str, outcome: EventOutcome) -> None:
    """Note a verified webhook as its platform's latest (ADR 0082 point 5); never fails it."""
    health: IntegrationHealthService = request.app.state.health_service
    clock: Clock = request.app.state.clock
    try:
        await health.note_inbound(InboundEvent(platform, clock.now(), kind, outcome.value))
    except Exception as exc:  # the event is recorded; its note must not turn into a 500
        log.warning("webhook.note_failed", source=platform.value, error=type(exc).__name__)
