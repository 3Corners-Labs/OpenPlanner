"""Inbound webhooks from Webex and WxCC (ADR 0010, 0017, 0018).

Authenticated by signature, not by an admin session: the secret is configured per
installation (``WEBEX_WEBHOOK_SECRET``, ``WXCC_WEBHOOK_SECRET``); without it the endpoint
does not exist (404). The body is size-capped and verified before it is parsed. Unknown
meetings, tasks and event types, and events seen before (replays), are acknowledged and
ignored, so the platforms do not retry. Every verified webhook is noted as its platform's latest
inbound event, for *Koppelingen* and *System status* (ADR 0082 point 5).

    POST /webhooks/webex   meetings started/ended, participants joined/left
    POST /webhooks/wxcc    task:new, task:connected, task:ended, task:failed
"""

import json
from typing import Any

import structlog
from fastapi import APIRouter, Request
from starlette.responses import Response

from app.adapters.webhooks import parse_webex, parse_wxcc, verify_webex, verify_wxcc
from app.domain.connection_health import InboundEvent, Platform
from app.domain.integration import EventOutcome, IntegrationService
from app.domain.integration_health import IntegrationHealthService
from app.domain.ports import Clock
from app.domain.security_log import INVALID_SIGNATURE, WEBHOOK_REJECTED
from app.web.protection import limiter
from app.web.security_events import report

router = APIRouter(prefix="/webhooks", include_in_schema=False)
log = structlog.get_logger()

MAX_BODY_BYTES = 64 * 1024
WEBHOOK_LIMIT = "600/minute"


async def _body(request: Request) -> bytes | None:
    """Return the raw body, or ``None`` if it is too large."""
    body = await request.body()
    return body if len(body) <= MAX_BODY_BYTES else None


def _json_object(body: bytes) -> dict[str, Any] | None:
    """Return the body as a JSON object, or ``None`` if it is not one."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _service(request: Request) -> IntegrationService:
    """Return the application's integration service."""
    service: IntegrationService = request.app.state.integration_service
    return service


async def _record_rejection(request: Request, source: str) -> None:
    """Record a rejected webhook as a deduplicated security event (ADR 0034 AU-4, ADR 0053).

    Only the source and the caller's pseudonym are recorded, never anything from the request.
    A failure there is logged; the caller still gets its 401.
    """
    await report(request, WEBHOOK_REJECTED, INVALID_SIGNATURE, detail=f"source:{source}")


@router.post("/webex")
@limiter.limit(WEBHOOK_LIMIT)
async def webex(request: Request) -> Response:
    """Record a Webex meeting or participant event."""
    secret = request.app.state.settings.webex_webhook_secret
    if secret is None:
        return Response(status_code=404)
    body = await _body(request)
    if body is None:
        return Response(status_code=413)
    if not verify_webex(body, request.headers.get("x-spark-signature"), secret.get_secret_value()):
        log.warning("webhook.rejected", source="webex")
        await _record_rejection(request, "webex")
        return Response(status_code=401)
    payload = _json_object(body)
    if payload is None:
        return Response(status_code=400)
    event = parse_webex(payload)
    clock: Clock = request.app.state.clock
    outcome = EventOutcome.IGNORED
    if event is not None:
        outcome = await _service(request).record_meeting_event(
            event.target_id, event.kind, clock.now(), event_key=event.event_key
        )
        log.info(
            "webhook.webex",
            kind=event.kind.value,
            outcome=outcome.value,
            meeting_id=event.target_id.split("_I_", 1)[0],
        )
    await _note(request, Platform.WEBEX, _webex_kind(payload), outcome)
    return Response(status_code=204)


@router.post("/wxcc")
@limiter.limit(WEBHOOK_LIMIT)
async def wxcc(request: Request) -> Response:
    """Record a WxCC task event."""
    secret = request.app.state.settings.wxcc_webhook_secret
    if secret is None:
        return Response(status_code=404)
    body = await _body(request)
    if body is None:
        return Response(status_code=413)
    clock: Clock = request.app.state.clock
    if not verify_wxcc(
        body,
        request.headers.get("x-webexcc-signature"),
        request.headers.get("x-webexcc-timestamp"),
        secret.get_secret_value(),
        clock.now(),
    ):
        log.warning("webhook.rejected", source="wxcc")
        await _record_rejection(request, "wxcc")
        return Response(status_code=401)
    payload = _json_object(body)
    if payload is None:  # pragma: no cover - verify_wxcc already requires an object
        return Response(status_code=400)
    event = parse_wxcc(payload)
    outcome = EventOutcome.IGNORED
    if event is not None:
        outcome = await _service(request).record_task_event(
            event.target_id, event.kind, clock.now(), event.detail, event_key=event.event_key
        )
        log.info("webhook.wxcc", kind=event.kind.value, outcome=outcome.value)
    await _note(request, Platform.WXCC, str(payload.get("type", ""))[:64], outcome)
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
