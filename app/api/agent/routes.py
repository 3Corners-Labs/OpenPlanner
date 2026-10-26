"""Read routes of the machine API (ADR 0023); none returns personal data.

    GET /api/agent/v1/me                            any valid token
    GET /api/agent/v1/groups                        availability:read
    GET /api/agent/v1/groups/{slug}/availability    availability:read on that group

Every route depends on exactly one ``RequireScope`` and is rate-limited per client.
Change requests (reschedule, cancel, confirmed by the citizen) follow in the next step.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.agent.auth import AgentContext, RequireScope, api_access, client_key
from app.api.agent.errors import ApiProblem
from app.api.agent.schemas import (
    AvailabilityResponse,
    ClientInfo,
    ErrorResponse,
    GroupInfo,
    SlotInfo,
)
from app.domain.authorization import ApiScope
from app.domain.services import BookingService
from app.i18n.dates import format_clock, format_day_short, local_day
from app.web.protection import agent_call_limit, limiter

router = APIRouter()

_REFUSALS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "`invalid_token`: missing, expired, revoked."},
    403: {"model": ErrorResponse, "description": "`insufficient_scope`."},
    429: {"model": ErrorResponse, "description": "`rate_limited`."},
    503: {"model": ErrorResponse, "description": "`temporarily_unavailable`: switched off."},
}

Read = Annotated[AgentContext, Depends(RequireScope(ApiScope.AVAILABILITY_READ))]
ReadGroup = Annotated[
    AgentContext, Depends(RequireScope(ApiScope.AVAILABILITY_READ, group_param="slug"))
]


@router.get(
    "/me", response_model=ClientInfo, responses=_REFUSALS, summary="Who am I", tags=["client"]
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def me(
    request: Request, context: Annotated[AgentContext, Depends(RequireScope(None))]
) -> ClientInfo:
    """Return the client behind the token, its organisation, groups and scopes."""
    caller = context.caller
    client = caller.client
    return ClientInfo(
        client_id=client.client_id,
        name=client.name,
        organisation=client.organisation,
        groups=sorted(client.groups) or None,
        scopes=sorted(caller.grant.scopes),
        token_expires_at=caller.expires_at,
    )


@router.get(
    "/groups",
    response_model=list[GroupInfo],
    responses=_REFUSALS,
    summary="List groups",
    tags=["availability"],
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def groups(request: Request, context: Read) -> list[GroupInfo]:
    """Return the groups the client may read the availability of. Scope `availability:read`."""
    found = await api_access(request).groups(context.caller, ApiScope.AVAILABILITY_READ)
    return [
        GroupInfo(
            slug=g.group.slug,
            name=g.group.name,
            timezone=g.group.timezone,
            slot_minutes=g.group.slot_minutes,
        )
        for g in found
    ]


@router.get(
    "/groups/{slug}/availability",
    response_model=AvailabilityResponse,
    responses={**_REFUSALS, 404: {"model": ErrorResponse, "description": "`group_not_found`."}},
    summary="Free appointment times of a group",
    tags=["availability"],
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def availability(
    request: Request,
    slug: str,
    context: ReadGroup,
    first: Annotated[date, Query(alias="from", description="First local date, inclusive.")],
    last: Annotated[date, Query(alias="to", description="Last local date, inclusive.")],
    lang: Annotated[str, Query(pattern="^(nl|en)$", description="Language of `label`.")] = "nl",
) -> AvailabilityResponse:
    """Return the free times between two local dates (at most 62 days).

    Scope `availability:read` on the group. The bot offers these to the caller; booking and
    changing go through the citizen's own confirmation (next step).
    """
    if context.group is None:  # pragma: no cover - RequireScope with a group returns it
        raise ApiProblem("invalid_request")
    service: BookingService = request.app.state.booking_service
    found = await service.availability(slug, first, last)
    tz = context.group.group.tz
    return AvailabilityResponse(
        group=slug,
        timezone=str(tz),
        slots=[
            SlotInfo(
                start=s.start,
                end=s.end,
                local_date=local_day(s.start, tz),
                local_time=s.start.astimezone(tz).strftime("%H:%M"),
                label=f"{format_day_short(local_day(s.start, tz), lang)} "
                f"{format_clock(s.start, tz, lang)}",
                remaining=s.remaining,
            )
            for s in found
        ],
    )
