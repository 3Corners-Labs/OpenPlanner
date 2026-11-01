"""Routes of the machine API (ADR 0023); none returns personal data.

    GET /api/agent/v1/me                            any valid token
    GET /api/agent/v1/groups                        availability:read
    GET /api/agent/v1/groups/{slug}/availability    availability:read on that group
    GET /api/agent/v1/configuration                 configuration:transfer (ADR 0089 point 7)
    PUT /api/agent/v1/configuration                 configuration:transfer

Every route depends on exactly one ``RequireScope`` and is rate-limited per client. The
configuration routes hand the package to ``ConfigurationPackageService`` as the portal does;
the rules (passphrase, size, installation in use, revision, audit) live there, not here.
Change requests (reschedule, cancel, confirmed by the participant) follow in a later step.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from starlette.responses import Response

from app.api.agent.auth import AgentContext, RequireScope, api_access, client_key
from app.api.agent.errors import ApiProblem
from app.api.agent.schemas import (
    AvailabilityResponse,
    ClientInfo,
    ConfigurationSummary,
    ErrorResponse,
    GroupInfo,
    SlotInfo,
)
from app.config import Settings
from app.domain.authorization import ApiScope
from app.domain.configuration_package import MEDIA_TYPE, package_filename
from app.domain.configuration_service import ConfigurationPackageService
from app.domain.errors import DomainError, ErrorCode
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
Transfer = Annotated[AgentContext, Depends(RequireScope(ApiScope.CONFIGURATION_TRANSFER))]
Passphrase = Annotated[
    str,
    Header(
        alias="X-Passphrase",
        description="The passphrase the package's secrets are sealed under (16 characters or "
        "more); never stored or logged.",
    ),
]

_PACKAGE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
    }
}


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
    changing go through the participant's own confirmation (next step).
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


@router.get(
    "/configuration",
    response_class=Response,
    responses={
        **_REFUSALS,
        200: {
            "content": {MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
            "description": "The configuration package, a SQLite file (ADR 0089).",
        },
        422: {
            "model": ErrorResponse,
            "description": "`invalid_request` (no `X-Passphrase`), "
            "`configuration_passphrase_short`.",
        },
    },
    summary="Export the configuration package",
    tags=["configuration"],
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def export_configuration(
    request: Request, context: Transfer, passphrase: Passphrase
) -> Response:
    """Return every configuration table of this installation, secrets sealed under the passphrase.

    Scope `configuration:transfer`, on the whole installation whatever organisation registered
    the client. The same package the superuser exports on *Advanced settings*; audited as that
    export is, with the client id as actor. No participant data: every state table is empty.
    """
    package = await _packages(request).export(context.caller.client.client_id, passphrase)
    current: Settings = request.app.state.settings
    name = package_filename(current.installation_name, request.app.state.clock.now())
    return Response(
        package,
        media_type=MEDIA_TYPE,
        headers={"content-disposition": f'attachment; filename="{name}"'},
    )


@router.put(
    "/configuration",
    response_model=ConfigurationSummary,
    responses={
        **_REFUSALS,
        409: {
            "model": ErrorResponse,
            "description": "`configuration_installation_in_use` (bookings, slots or an active "
            "closure exist), `configuration_package_version` (another schema revision).",
        },
        413: {"model": ErrorResponse, "description": "`configuration_package_too_large`."},
        422: {
            "model": ErrorResponse,
            "description": "`invalid_request` (no `X-Passphrase`), "
            "`configuration_package_invalid`, `configuration_passphrase_wrong`.",
        },
    },
    openapi_extra=_PACKAGE_BODY,
    summary="Import a configuration package",
    tags=["configuration"],
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def import_configuration(
    request: Request, context: Transfer, passphrase: Passphrase
) -> ConfigurationSummary:
    """Replace every configuration table of this installation with the package in the body.

    Scope `configuration:transfer`. Only into an installation that is not in use (no booking,
    slot or active closure); the package must carry the running schema revision; the body is
    refused above `CONFIGURATION_PACKAGE_MAX_MB`. Audited with the counts, never a value.
    """
    current: Settings = request.app.state.settings
    package = await _body(request, current.configuration_package_max_mb * 1024 * 1024)
    summary = await _packages(request).import_(context.caller.client.client_id, package, passphrase)
    return ConfigurationSummary(
        installation=summary.installation,
        app_version=summary.app_version,
        created_at=summary.created_at,
        revision=summary.revision,
        counts=summary.counts,
        secrets=summary.secrets,
    )


async def _body(request: Request, limit: int) -> bytes:
    """Return the request body, refusing one above ``limit`` before all of it is held.

    Raises:
        DomainError: ``configuration_package_too_large``, from the declared length when there
            is one, else as soon as the read passes the limit.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_TOO_LARGE, "package")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_TOO_LARGE, "package")
    return bytes(body)


def _packages(request: Request) -> ConfigurationPackageService:
    """Return the application's configuration package service (shared with the portal)."""
    service: ConfigurationPackageService = request.app.state.configuration_packages
    return service
