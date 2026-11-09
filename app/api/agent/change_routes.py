"""Change requests of the machine API (ADR 0023 step 2); none reads or returns personal data.

    POST /api/agent/v1/groups/{slug}/change-requests   bookings:request_change on that group
    GET  /api/agent/v1/change-requests/{id}            bookings:request_change, own requests

The caller names the booking by phone number and appointment code; the answer is the same
whether or not they match a booking (``ChangeRequestService``). The participant confirms the
change through their own message; the API never changes a booking.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from starlette.responses import JSONResponse

from app.api.agent.auth import AgentContext, RequireScope, client_key
from app.api.agent.errors import ApiProblem
from app.api.agent.schemas import ChangeRequestBody, ChangeRequestInfo, ErrorResponse
from app.domain.authorization import ApiScope
from app.domain.change_request_service import ChangeRequestService
from app.domain.change_requests import ChangeAction, ChangeRequest
from app.domain.ports import Clock
from app.web.protection import agent_call_limit, change_request_limit, limiter

router = APIRouter(tags=["change requests"])

_REFUSALS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "`invalid_token`: missing, expired, revoked."},
    403: {"model": ErrorResponse, "description": "`insufficient_scope`."},
    429: {"model": ErrorResponse, "description": "`rate_limited`."},
    503: {"model": ErrorResponse, "description": "`temporarily_unavailable`: switched off."},
}

Ask = Annotated[
    AgentContext,
    Depends(RequireScope(ApiScope.BOOKINGS_REQUEST_CHANGE, group_param="slug")),
]
Read = Annotated[AgentContext, Depends(RequireScope(ApiScope.BOOKINGS_REQUEST_CHANGE))]
RequestId = Annotated[str, Path(max_length=40, pattern="^chr_[A-Za-z0-9_-]+$")]


def _service(request: Request) -> ChangeRequestService:
    """Return the application's change request service."""
    service: ChangeRequestService = request.app.state.change_request_service
    return service


def _info(found: ChangeRequest, now: datetime) -> ChangeRequestInfo:
    """Describe a request: its id, action, status and expiry; never the booking."""
    return ChangeRequestInfo(
        id=found.public_id,
        action=found.action.value,
        start=found.target_start,
        status=found.status(now).value,
        expires_at=found.expires_at,
    )


@router.post(
    "/groups/{slug}/change-requests",
    response_model=ChangeRequestInfo,
    status_code=202,
    responses={
        **_REFUSALS,
        404: {"model": ErrorResponse, "description": "`group_not_found`."},
        409: {
            "model": ErrorResponse,
            "description": "`slot_unavailable`: the requested time is not free.",
        },
        422: {
            "model": ErrorResponse,
            "description": "`invalid_request`, `invalid_contact` (no valid phone number), "
            "`invalid_appointment_code`, `change_requests_disabled` (the organisation has not "
            "switched change requests on).",
        },
    },
    summary="Ask to cancel or move an appointment",
)
@limiter.limit(change_request_limit, key_func=client_key)
@limiter.limit(agent_call_limit, key_func=client_key)
async def create(
    request: Request, slug: str, body: ChangeRequestBody, context: Ask
) -> JSONResponse:
    """Ask the participant to confirm a cancellation or a new time.

    Scope `bookings:request_change` on the group. Name the appointment by the caller's phone
    number **and** appointment code. The answer is `202` with a request id, whether or not they
    match an appointment that can still be changed: tell the caller to look for the message and
    confirm there. Nothing changes until they do; `expires_at` is when the request lapses.
    """
    if context.group is None:  # pragma: no cover - RequireScope with a group returns it
        raise ApiProblem("invalid_request")
    action = ChangeAction(body.action)
    if (action is ChangeAction.RESCHEDULE) != (body.start is not None):
        raise ApiProblem("invalid_request")
    found = await _service(request).request(
        context.caller, context.group, action, body.phone, body.code, body.start
    )
    clock: Clock = request.app.state.clock
    info = _info(found, clock.now())
    return JSONResponse(info.model_dump(mode="json"), status_code=202)


@router.get(
    "/change-requests/{request_id}",
    response_model=ChangeRequestInfo,
    responses={
        **_REFUSALS,
        404: {
            "model": ErrorResponse,
            "description": "`change_request_not_found`: no such request of this client.",
        },
    },
    summary="Status of a change request",
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def status(request: Request, request_id: RequestId, context: Read) -> ChangeRequestInfo:
    """Return `pending`, `confirmed` or `expired`. Scope `bookings:request_change`.

    Only the client that made the request can read it. A request that matched no appointment
    stays `pending` until it expires, like one the participant has not confirmed yet.
    """
    found = await _service(request).status(context.caller, request_id)
    clock: Clock = request.app.state.clock
    return _info(found, clock.now())
