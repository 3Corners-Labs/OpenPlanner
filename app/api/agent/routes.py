"""Routes of the machine API (ADR 0023); none returns personal data.

    GET /api/agent/v1/me                            any valid token
    GET /api/agent/v1/groups                        availability:read
    GET /api/agent/v1/groups/{slug}/availability    availability:read on that group
    GET /api/agent/v1/reports/usage                reports:read (ADR 0117 point 13)
    GET /api/agent/v1/configuration                 configuration:transfer (ADR 0099)
    POST /api/agent/v1/configuration/plan           configuration:transfer
    POST /api/agent/v1/configuration/apply          configuration:transfer
    PUT /api/agent/v1/configuration                 configuration:transfer

Every route depends on exactly one ``RequireScope`` and is rate-limited per client. The
configuration routes hand the document to ``ConfigurationDocumentService`` as the portal does
(a SQLite package of ADR 0089 goes through ``ConfigurationPackageService``, converted to a
document); the rules (passphrase, size, rights, in-use, plan hash, audit) live there.
Change requests (reschedule, cancel, confirmed by the participant) follow in a later step.
"""

import json
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from starlette.responses import JSONResponse, Response

from app.adapters.configuration_json import MEDIA_TYPE, document_response
from app.api.agent.auth import AgentContext, RequireScope, api_access, client_key
from app.api.agent.errors import ApiProblem
from app.api.agent.schemas import (
    AvailabilityResponse,
    ClientInfo,
    ConfigurationApplied,
    ConfigurationPlan,
    ConfigurationSummary,
    ErrorResponse,
    GroupInfo,
    PlanChange,
    PlanRefusal,
    SlotInfo,
    UsagePageResponse,
)
from app.config import Settings
from app.domain.authorization import ApiScope
from app.domain.configuration_changes import ChangeAction
from app.domain.configuration_document import Scope, document_filename
from app.domain.configuration_document_service import (
    Actor,
    ApplyResult,
    ConfigurationDocumentService,
)
from app.domain.configuration_plan import Plan
from app.domain.configuration_service import ConfigurationPackageService
from app.domain.csv_export import Cell, to_csv
from app.domain.errors import DomainError, ErrorCode
from app.domain.services import BookingService
from app.domain.usage_api_service import UsageApiService, UsagePage
from app.domain.usage_report_csv import TIDY_HEADER, TIDY_VERSION
from app.i18n.dates import format_clock, format_day_short, local_day
from app.web.protection import agent_call_limit, limiter, reports_limit
from app.web.request_body import read_capped

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
PlainSecrets = Annotated[
    bool,
    Header(
        alias="X-Plain-Secrets",
        description="`true` on an export writes the secrets in clear text, without a passphrase "
        "(ADR 0099 point 8a). Refused with `configuration_plaintext_disabled` unless the host "
        "allows it. The response must be kept as private as the secrets.",
    ),
]
Passphrase = Annotated[
    str | None,
    Header(
        alias="X-Passphrase",
        description="The passphrase the document's secrets are sealed under (16 characters or "
        "more): on an export it seals them (without it the document holds none), on a plan or "
        "an apply it opens them. Never stored or logged.",
    ),
]
PlanHash = Annotated[
    str | None,
    Header(
        alias="X-Plan-Hash",
        description="The `plan_hash` of the plan you reviewed; an apply that deletes needs it, "
        "and any apply is refused when the plan is no longer that one.",
    ),
]

_PACKAGE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"application/vnd.sqlite3": {"schema": {"type": "string", "format": "binary"}}},
    }
}

_DOCUMENT_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}},
            "application/json": {"schema": {"type": "object"}},
        },
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
            "description": "The configuration document of the platform (ADR 0099).",
        },
        422: {
            "model": ErrorResponse,
            "description": "`configuration_passphrase_short`: the passphrase is under 16 "
            "characters.",
        },
    },
    summary="Export the configuration as a document",
    tags=["configuration"],
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def export_configuration(
    request: Request, context: Transfer, passphrase: Passphrase = None, plain: PlainSecrets = False
) -> Response:
    """Return the configuration of the whole installation as a JSON document.

    Scope `configuration:transfer`, on the whole installation whatever organisation registered
    the client. With `X-Passphrase` the secrets are in the document, sealed under it; without,
    the document holds none. With `X-Plain-Secrets: true` they are in it in clear text (no
    passphrase), when the host allows that. Audited with the client id as actor. No participant
    data.
    """
    actor = Actor.api_client(context.caller.client.client_id)
    document = await _documents(request).export(
        actor, Scope(), passphrase, secrets=passphrase is not None or plain, plain=plain
    )
    current: Settings = request.app.state.settings
    name = document_filename(current.installation_name, request.app.state.clock.now())
    return document_response(document, name)


@router.post(
    "/configuration/plan",
    response_model=ConfigurationPlan,
    responses={
        **_REFUSALS,
        413: {"model": ErrorResponse, "description": "`configuration_package_too_large`."},
        422: {
            "model": ErrorResponse,
            "description": "`invalid_configuration` (the document is not valid), "
            "`configuration_passphrase_short`, `configuration_passphrase_wrong`, "
            "`configuration_package_invalid`, `configuration_out_of_scope`.",
        },
    },
    openapi_extra=_DOCUMENT_BODY,
    summary="Plan a configuration document",
    tags=["configuration"],
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def plan_configuration(
    request: Request, context: Transfer, passphrase: Passphrase = None
) -> ConfigurationPlan:
    """Say what the document in the body would change, without writing anything.

    Scope `configuration:transfer`. The answer lists every change by action, kind, path and
    field names, and every refusal; a plan with a refusal has `ok` false. Never a value.
    """
    current: Settings = request.app.state.settings
    body = await _body(request, current.configuration_package_max_mb * 1024 * 1024)
    plan = await _documents(request).plan(
        Actor.api_client(context.caller.client.client_id), body, passphrase, scope=Scope()
    )
    return _plan_model(plan)


@router.post(
    "/configuration/apply",
    response_model=ConfigurationApplied,
    responses={
        **_REFUSALS,
        409: {
            "model": ErrorResponse,
            "description": "`configuration_plan_stale`: the plan changed since `X-Plan-Hash`, or "
            "a plan that deletes came without it.",
        },
        413: {"model": ErrorResponse, "description": "`configuration_package_too_large`."},
        422: {
            "model": ConfigurationApplied,
            "description": "The plan holds a refusal and nothing was written; or one of "
            "`invalid_configuration`, `configuration_passphrase_*`, "
            "`configuration_too_many_changes`, `configuration_out_of_scope`.",
        },
    },
    openapi_extra=_DOCUMENT_BODY,
    summary="Apply a configuration document",
    tags=["configuration"],
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def apply_configuration(
    request: Request,
    context: Transfer,
    passphrase: Passphrase = None,
    plan_hash: PlanHash = None,
) -> Response:
    """Write the document in the body: all of it, or nothing.

    Scope `configuration:transfer`. Plans again inside the write lock; a plan that deletes
    needs the `X-Plan-Hash` of the plan you reviewed. Audited per change with the client id.
    """
    return await _apply(request, context, passphrase, plan_hash)


async def _apply(
    request: Request, context: AgentContext, passphrase: str | None, plan_hash: str | None
) -> Response:
    """Apply the document in the body; the answer of `POST …/apply` and of `PUT`."""
    current: Settings = request.app.state.settings
    body = await _body(request, current.configuration_package_max_mb * 1024 * 1024)
    return _applied(
        await _documents(request).apply(
            Actor.api_client(context.caller.client.client_id),
            body,
            passphrase,
            plan_hash,
            scope=Scope(),
        )
    )


@router.put(
    "/configuration",
    response_model=ConfigurationApplied,
    responses={
        **_REFUSALS,
        409: {
            "model": ErrorResponse,
            "description": "`configuration_plan_stale`: the plan changed since `X-Plan-Hash`, or "
            "a plan that deletes came without it.",
        },
        413: {"model": ErrorResponse, "description": "`configuration_package_too_large`."},
        422: {
            "model": ConfigurationApplied,
            "description": "The plan holds a refusal and nothing was written; or one of "
            "`invalid_configuration`, `configuration_passphrase_*`, "
            "`configuration_too_many_changes`, `configuration_out_of_scope`.",
        },
    },
    openapi_extra=_DOCUMENT_BODY,
    summary="Apply a configuration document",
    tags=["configuration"],
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def put_configuration(
    request: Request,
    context: Transfer,
    passphrase: Passphrase = None,
    plan_hash: PlanHash = None,
) -> Response:
    """Apply the body as the platform's configuration: the same as `POST /configuration/apply`.

    Scope `configuration:transfer`. Kept for the pipelines of ADR 0089 step 3, which `PUT` the
    configuration; the document's own `mode` says whether what it does not name is deleted
    (`replace`), and a plan that deletes needs the `X-Plan-Hash` of the plan you reviewed.
    """
    return await _apply(request, context, passphrase, plan_hash)


@router.put(
    "/configuration/package",
    response_model=ConfigurationSummary,
    responses={
        **_REFUSALS,
        409: {
            "model": ErrorResponse,
            "description": "`configuration_package_version` (another schema revision).",
        },
        413: {"model": ErrorResponse, "description": "`configuration_package_too_large`."},
        422: {
            "model": ErrorResponse,
            "description": "`configuration_package_invalid`, `configuration_passphrase_wrong`, "
            "`configuration_passphrase_short` (no `X-Passphrase`), and the refusal that stopped "
            "the plan.",
        },
    },
    openapi_extra=_PACKAGE_BODY,
    summary="Replace the configuration with an old SQLite package",
    tags=["configuration"],
)
@limiter.limit(agent_call_limit, key_func=client_key)
async def import_package(
    request: Request, context: Transfer, passphrase: Passphrase = None
) -> ConfigurationSummary:
    """Apply an ADR 0089 SQLite package as a replacing document of the platform.

    Scope `configuration:transfer`. Only for one release after the JSON document replaced the
    package (ADR 0099 point 14); the passphrase is required; the package must carry the running
    schema revision. The call is the confirmation: what the converted document deletes is
    deleted unless something refers to it. Audited with the counts, never a value.
    """
    if passphrase is None:
        raise DomainError(ErrorCode.CONFIGURATION_PASSPHRASE_SHORT, "passphrase")
    current: Settings = request.app.state.settings
    body = await _body(request, current.configuration_package_max_mb * 1024 * 1024)
    summary = await _packages(request).import_(
        Actor.api_client(context.caller.client.client_id), body, passphrase
    )
    return ConfigurationSummary(
        installation=summary.installation,
        app_version=summary.app_version,
        created_at=summary.created_at,
        revision=summary.revision,
        counts=summary.counts,
        secrets=summary.secrets,
    )


def _plan_model(plan: Plan) -> ConfigurationPlan:
    """Describe a plan for the API: names and codes, never a value."""
    return ConfigurationPlan(
        ok=plan.ok,
        plan_hash=plan.digest,
        changes=[
            PlanChange(
                action=c.action.value,
                kind=c.kind,
                path=c.path,
                fields=[] if c.action is ChangeAction.DELETE else list(c.fields),
            )
            for c in plan.changes
        ],
        refusals=[
            PlanRefusal(path=r.path, code=r.code.value, field=r.field) for r in plan.refusals
        ],
    )


def _applied(result: ApplyResult) -> JSONResponse:
    """Answer an apply: 200 when it was written, 422 with the refusing plan when not."""
    body = ConfigurationApplied(
        applied=result.applied, settings_version=result.version, plan=_plan_model(result.plan)
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if result.applied else 422)


UsageRead = Annotated[AgentContext, Depends(RequireScope(ApiScope.REPORTS_READ))]
UsageFormat = Literal["json", "csv", "ndjson"]


def _usage_headers(page: UsagePage) -> dict[str, str]:
    """Return the headers every usage answer carries: validator, privacy, paging, freshness."""
    headers = {"cache-control": "private", "etag": f'"{page.etag}"'}
    if page.next_cursor is not None:
        headers["x-next-cursor"] = page.next_cursor
    if page.as_of is not None:
        headers["x-as-of"] = page.as_of.isoformat()
    return headers


def _usage_row(row: tuple[Cell, ...]) -> dict[str, Cell]:
    """Return a tidy row as an object keyed by the column names."""
    return dict(zip(TIDY_HEADER, row, strict=True))


@router.get(
    "/reports/usage",
    response_model=UsagePageResponse,
    responses={
        **_REFUSALS,
        200: {
            "content": {
                "text/csv": {"schema": {"type": "string"}},
                "application/x-ndjson": {"schema": {"type": "string"}},
            },
            "description": "A page of hourly usage figures; `format` chooses JSON (default), "
            "`csv` (the columns of `openplanner.usage/1`) or `ndjson`.",
        },
        304: {"description": "`If-None-Match` matches: nothing changed since the last call."},
        404: {
            "model": ErrorResponse,
            "description": "`group_not_found`: not a group of the client.",
        },
        422: {
            "model": ErrorResponse,
            "description": "`invalid_range`: a backwards or too wide range, an unknown metric "
            "or cut, or a cursor that is none.",
        },
    },
    summary="Hourly usage figures",
    tags=["reports"],
)
@limiter.limit(reports_limit, key_func=client_key)
async def usage(
    request: Request,
    context: UsageRead,
    first: Annotated[date, Query(alias="from", description="First day, inclusive (group-local).")],
    last: Annotated[date, Query(alias="to", description="Last day, inclusive (group-local).")],
    metric: Annotated[
        str | None, Query(max_length=3, pattern="^[A-Z][0-9]{1,2}$", description="One metric id.")
    ] = None,
    group: Annotated[
        str | None, Query(max_length=80, description="One group slug of the client.")
    ] = None,
    cut: Annotated[
        str | None,
        Query(
            max_length=20,
            description="Keep one cut column (`booking_kind`, `dispatch_kind`, `subject`, "
            "`locale`, `channel`, `outcome`, `actor_class`, `reason`); the rest are added up. "
            "Without it every cut is added up.",
        ),
    ] = None,
    fmt: Annotated[UsageFormat, Query(alias="format", description="Answer format.")] = "json",
    cursor: Annotated[
        str | None, Query(max_length=400, description="`next_cursor` of the previous page.")
    ] = None,
) -> Response:
    """Return one page of the hourly usage figures of the client's groups. Scope `reports:read`.

    The figures are counts and sums, never personal data; a cell under the installation's
    small-count threshold is not returned. The page size and the widest range are settings of
    the installation. Poll with `If-None-Match`.
    """
    page = await _usage(request).read(
        context.caller,
        first_day=first,
        last_day=last,
        metric=metric,
        group=group,
        cut=cut,
        cursor=cursor,
    )
    headers = _usage_headers(page)
    if request.headers.get("if-none-match", "").strip() == headers["etag"]:
        return Response(status_code=304, headers=headers)
    if fmt == "csv":
        return Response(
            to_csv(TIDY_HEADER, page.rows).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers=headers,
        )
    if fmt == "ndjson":
        lines = "".join(json.dumps(_usage_row(r), separators=(",", ":")) + "\n" for r in page.rows)
        return Response(lines, media_type="application/x-ndjson", headers=headers)
    body = {
        "schema": TIDY_VERSION,
        "as_of": page.as_of.isoformat() if page.as_of else None,
        "next_cursor": page.next_cursor,
        "rows": [_usage_row(r) for r in page.rows],
    }
    return JSONResponse(body, headers=headers)


def _usage(request: Request) -> UsageApiService:
    """Return the application's usage API service."""
    service: UsageApiService = request.app.state.usage_api_service
    return service


async def _body(request: Request, limit: int) -> bytes:
    """Return the request body, refusing one above ``limit`` before all of it is held.

    Raises:
        DomainError: ``configuration_package_too_large``, from the declared length when there
            is one, else as soon as the read passes the limit.
    """
    body = await read_capped(request, limit)
    if body is None:
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_TOO_LARGE, "package")
    return body


def _documents(request: Request) -> ConfigurationDocumentService:
    """Return the application's configuration document service (shared with the portal)."""
    service: ConfigurationDocumentService = request.app.state.configuration_documents
    return service


def _packages(request: Request) -> ConfigurationPackageService:
    """Return the application's configuration package service (shared with the portal)."""
    service: ConfigurationPackageService = request.app.state.configuration_packages
    return service
