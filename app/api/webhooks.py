"""Inbound webhooks from Webex and WxCC (ADR 0010, 0017, 0018).

Authenticated by signature, not by an admin session: the secret is configured per
installation (``WEBEX_WEBHOOK_SECRET``, ``WXCC_WEBHOOK_SECRET``); without it the endpoint
does not exist (404). The body is size-capped and verified before it is parsed. Unknown
meetings, tasks and event types, and Webex events seen before (replays), are acknowledged and
ignored, so the platforms do not retry.

    POST /webhooks/webex   meetings started/ended, participants joined/left
    POST /webhooks/wxcc    task:connected, task:ended, task:failed
"""

import json
from typing import Any

import structlog
from fastapi import APIRouter, Request
from starlette.responses import Response

from app.adapters.webhooks import parse_webex, parse_wxcc, verify_webex, verify_wxcc
from app.domain.audit import Outcome
from app.domain.integration import IntegrationService
from app.domain.ports import Clock
from app.domain.security_log import INVALID_SIGNATURE, WEBHOOK_REJECTED, SecurityLog
from app.web.protection import limiter

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
    """Record a rejected webhook as a deduplicated security event (ADR 0034, AU-4).

    Only the source is recorded, never anything from the request. A failure here is logged;
    the caller still gets its 401.
    """
    security: SecurityLog = request.app.state.security_log
    try:
        await security.record(
            WEBHOOK_REJECTED, Outcome.DENIED, reason=INVALID_SIGNATURE, detail=f"source:{source}"
        )
    except Exception as exc:  # the rejection itself must not turn into a 500
        log.warning("security_log.failed", error=type(exc).__name__)


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
    if event is not None:
        clock: Clock = request.app.state.clock
        outcome = await _service(request).record_meeting_event(
            event.target_id, event.kind, clock.now(), event_key=event.event_key
        )
        log.info("webhook.webex", kind=event.kind.value, outcome=outcome.value)
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
    if event is not None:
        outcome = await _service(request).record_task_event(
            event.target_id, event.kind, clock.now(), event.detail
        )
        log.info("webhook.wxcc", kind=event.kind.value, outcome=outcome.value)
    return Response(status_code=204)
