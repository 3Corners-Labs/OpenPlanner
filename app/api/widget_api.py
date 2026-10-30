"""Widget API: the booking flow for ``<gov-booking>`` on a host website (ADR 0035 §2–4).

Part of ``/api/v1``, so it is in the OpenAPI spec, tested by schemathesis and changed only
additively. It reuses the services and views of the standalone pages (slot picker, contact
form, closure banner), so rules and texts cannot drift between the two shells.

    GET     /api/v1/widget/texts                        texts before the group   reads limit
    GET     /api/v1/widget/{org}/{group}/               group, form and texts    reads limit
    GET     /api/v1/widget/{org}/{group}/availability   month and times of a day reads limit
    POST    /api/v1/widget/{org}/{group}/bookings       book                     bookings limit
    OPTIONS /api/v1/widget/{org}/{group}/bookings       CORS preflight           reads limit

``/texts`` holds only the few texts the Web Component shows before it knows whether the group
may be embedded here (loading, unavailable, retry): public catalogue text, answered to every
origin. ``WidgetRoute`` wraps every group route:

- The group must exist, belong to ``{org}`` and be switched on for embedding; otherwise
  ``404 group_not_found``, the same for all three cases.
- A request with an ``Origin`` header must come from the organisation's allowed origins
  (``403 origin_not_allowed``). A booking must carry one: it replaces the same-origin check of
  the standalone form (``require_allowed_origin``). Requests without ``Origin`` (curl, the
  OpenAPI tests) may read, as they may read the standalone page.
- Allowed origins get ``Access-Control-Allow-Origin`` on every answer, errors included,
  never ``*`` and never with credentials: the widget sends no cookies.
- Errors are JSON codes (``WidgetError``); they never echo input.
- During maintenance (ADR 0038) the group routes answer ``503 maintenance`` with
  ``Retry-After``, by the rule in ``app.api.maintenance``; ``/texts`` and the preflight stay open.
"""

import json
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse, Response

from app.api.contact_form import HONEYPOT_FIELD, contact_fields
from app.api.labels import slot_label
from app.api.maintenance import WidgetMaintenance, retry_after
from app.api.slot_picker import PickerView, picker, slot_value
from app.api.widget_schemas import (
    WidgetAvailability,
    WidgetBooking,
    WidgetBookingRequest,
    WidgetClosure,
    WidgetDay,
    WidgetError,
    WidgetField,
    WidgetGroup,
    WidgetMonth,
    WidgetTexts,
    WidgetTime,
    WidgetTimeGroup,
    WidgetTimes,
    WidgetWeekday,
)
from app.domain.embedding_service import EmbeddingService, WidgetAccess
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Group
from app.domain.services import BookingService, ContactInput
from app.web.closure_labels import closure_view
from app.web.errors import status_for
from app.web.locale import RequestLocale, resolve_locale
from app.web.organisation_settings import follow_group_from_path
from app.web.protection import booking_limit, limiter, read_limit
from app.web.slot_values import parse_start

log = structlog.get_logger()

PREFIX = "/api/v1/widget"
MAX_BODY_BYTES = 4096
"""A booking is four short strings; anything larger is refused before parsing."""

PREFLIGHT_MAX_AGE = "600"

TEXT_KEYS = (
    "widget.region",
    "widget.loading",
    "widget.unavailable",
    "widget.maintenance",
    "widget.retry",
    "widget.more",
    "widget.done_title",
    "widget.error_summary",
    "steps.label",
    "steps.time",
    "steps.details",
    "steps.done",
    "steps.completed",
    "picker.previous_month",
    "picker.next_month",
    "picker.today",
    "picker.day_free",
    "picker.day_full",
    "picker.day_later",
    "picker.legend_free",
    "picker.legend_selected",
    "picker.legend_later",
    "picker.legend",
    "picker.no_times_title",
    "picker.no_times",
    "picker.first",
    "picker.first_choose",
    "book.title",
    "book.selected",
    "book.change_slot",
    "book.privacy",
    "form.optional",
    "form.submit",
    "form.honeypot",
    "error.title",
    "error.slot_full",
    "error.slot_unavailable",
    "error.outside_booking_window",
    "error.group_closed",
    "error.database_busy",
    "error.rate_limited",
    "error.invalid_request",
    "error.field.name",
    "error.field.email",
    "error.field.phone",
)
"""Catalogue keys the Web Component shows; sent with the group in the requested locale."""

BASIC_TEXT_KEYS = (
    "widget.region",
    "widget.loading",
    "widget.unavailable",
    "widget.maintenance",
    "widget.retry",
)
"""What ``/texts`` returns: enough to say that the calendar is loading, closed for maintenance
or not available."""

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": WidgetError, "description": "`origin_not_allowed`."},
    404: {"model": WidgetError, "description": "`group_not_found`: unknown or not embeddable."},
    422: {"model": WidgetError, "description": "`invalid_request`."},
    429: {"model": WidgetError, "description": "`rate_limited`."},
    503: {"model": WidgetError, "description": "`maintenance`, with `Retry-After`."},
}


class WidgetProblem(Exception):  # noqa: N818 - an answer, like HTTPException
    """Refuse a widget request with a code.

    Args:
        code: The error code.
        status: The HTTP status.
        field: For a contact input, the field it concerns.
    """

    def __init__(self, code: str, status: int, field: str | None = None) -> None:
        """Store the code, status and field."""
        super().__init__(code)
        self.code = code
        self.status = status
        self.field = field


def problem(code: str, status: int, field: str | None = None) -> JSONResponse:
    """Return ``WidgetError`` as JSON with ``status``."""
    return JSONResponse(WidgetError(error=code, field=field).model_dump(), status_code=status)


def embedding_service(request: Request) -> EmbeddingService:
    """Return the application's embedding service."""
    service: EmbeddingService = request.app.state.embedding_service
    return service


def _booking_service(request: Request) -> BookingService:
    """Return the application's booking service."""
    service: BookingService = request.app.state.booking_service
    return service


def cors_headers(origin: str) -> dict[str, str]:
    """Return the headers that let ``origin`` (already checked) read the answer."""
    return {"access-control-allow-origin": origin, "vary": "Origin"}


async def check_access(request: Request) -> WidgetAccess:
    """Return the group of the request and whether its origin is allowed.

    Raises:
        DomainError: ``group_not_found``.
        WidgetProblem: ``origin_not_allowed`` for a foreign origin, or for a write without
            one (``require_allowed_origin``).
    """
    origin = request.headers.get("origin")
    access = await embedding_service(request).widget_group(
        request.path_params.get("org", ""), request.path_params.get("group", ""), origin
    )
    if not access.origin_allowed and (origin is not None or request.method == "POST"):
        # Origins are not personal data; logging them shows misconfigured hosts.
        log.info("widget.origin_refused", origin=(origin or "")[:200], group=access.group.slug)
        raise WidgetProblem("origin_not_allowed", 403)
    request.state.widget_access = access
    return access


class WidgetRoute(APIRoute):
    """Route class of the widget API: access check, CORS headers and JSON errors."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Wrap the handler; see the module docstring."""
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            access: WidgetAccess | None = None
            try:
                access = await check_access(request)
                response = await handler(request)
            except WidgetProblem as refused:
                response = problem(refused.code, refused.status, refused.field)
            except DomainError as error:
                status = 404 if error.code is ErrorCode.GROUP_NOT_FOUND else status_for(error)
                field = error.field if error.code is ErrorCode.INVALID_CONTACT else None
                response = problem(error.code.value, status, field)
            except RequestValidationError as invalid:
                # Only the names of the offending parameters, never their values.
                names = sorted({str(e["loc"][-1]) for e in invalid.errors() if e.get("loc")})
                log.info("widget.invalid_request", fields=",".join(names)[:200])
                response = problem("invalid_request", 422)
            except RateLimitExceeded:
                response = problem("rate_limited", 429)
                response.headers["retry-after"] = "60"
            except WidgetMaintenance:
                response = problem("maintenance", 503)
                response.headers.update(retry_after(request))
            response.headers["cache-control"] = "no-store"
            response.headers.setdefault("vary", "Origin")
            origin = request.headers.get("origin")
            if access is not None and access.origin_allowed and origin is not None:
                response.headers.update(cors_headers(origin))
            return response

        return route


router = APIRouter(
    prefix=PREFIX,
    route_class=WidgetRoute,
    tags=["widget"],
    dependencies=[Depends(follow_group_from_path)],
)
texts_router = APIRouter(prefix=PREFIX, tags=["widget"])
Locale = Annotated[RequestLocale, Depends(resolve_locale)]


@texts_router.get("/texts", response_model=WidgetTexts, summary="Texts before the group")
@limiter.limit(read_limit)
async def basic_texts(request: Request, locale: Locale) -> JSONResponse:
    """Return the texts for loading and for a calendar that is not available here."""
    body = WidgetTexts(locale=locale.code, texts={k: locale.gettext(k) for k in BASIC_TEXT_KEYS})
    return JSONResponse(
        body.model_dump(),
        headers={"access-control-allow-origin": "*", "cache-control": "public, max-age=300"},
    )


def _group(request: Request) -> Group:
    """Return the group ``check_access`` found for this request."""
    access: WidgetAccess = request.state.widget_access
    return access.group


def _texts(locale: RequestLocale, group: Group) -> dict[str, str]:
    """Return the widget's texts in ``locale``; ``{name}`` is the group's name."""
    return {key: locale.gettext(key, name=group.name) for key in TEXT_KEYS}


@router.get("/{org}/{group}/", response_model=WidgetGroup, responses=_ERRORS, summary="Group")
@limiter.limit(read_limit)
async def group_info(request: Request, org: str, group: str, locale: Locale) -> WidgetGroup:
    """Return the group's name, contact form, closure notice and the widget's texts."""
    found = _group(request)
    service = _booking_service(request)
    closure = await service.closure_notice(found)
    banner = (
        closure_view(closure, found, locale, request.app.state.clock.now()) if closure else None
    )
    base = request.app.state.settings.public_base_url.rstrip("/")
    return WidgetGroup(
        name=found.name,
        locale=locale.code,
        intro=locale.gettext("group.intro", minutes=found.slot_minutes),
        page_url=f"{base}/g/{found.slug}",
        closure=WidgetClosure(
            title=banner.title,
            period=banner.period,
            heading=banner.heading,
            paragraphs=banner.paragraphs,
            active=banner.active,
        )
        if banner
        else None,
        fields=[
            WidgetField(
                name=f.name,
                type=f.type,
                autocomplete=f.autocomplete,
                label=f.label,
                required=f.required,
                hint=f.hint,
            )
            for f in contact_fields(found, locale, [], {})
        ],
        honeypot=HONEYPOT_FIELD,
        texts=_texts(locale, found),
    )


def _availability(view: PickerView, locale: RequestLocale) -> WidgetAvailability:
    """Map the standalone page's picker to the widget's JSON."""
    month = view.month
    times = view.times
    return WidgetAvailability(
        month=WidgetMonth(
            month=month.iso,
            heading=month.heading,
            previous=month.previous_iso,
            next=month.next_iso,
            weekdays=[WidgetWeekday(short=s, name=n) for s, n in month.weekdays],
            weeks=[
                [
                    WidgetDay(
                        date=cell.iso,
                        number=int(cell.number),
                        label=cell.label,
                        free=cell.href is not None,
                        today=cell.today,
                        later=cell.later,
                    )
                    if cell.number
                    else None
                    for cell in week
                ]
                for week in month.weeks
            ],
            horizon=locale.gettext("picker.horizon", days=month.horizon_days),
            has_later=month.has_later,
        ),
        times=WidgetTimes(
            date=times.iso,
            heading=times.heading,
            count=times.count,
            groups=[
                WidgetTimeGroup(
                    heading=part.heading,
                    times=[WidgetTime(start=s.value, label=s.label) for s in part.slots],
                )
                for part in times.groups
            ],
        )
        if times
        else None,
        first=WidgetTime(start=view.first.value, label=view.first.label) if view.first else None,
    )


@router.get(
    "/{org}/{group}/availability",
    response_model=WidgetAvailability,
    responses=_ERRORS,
    summary="Month and times of a day",
)
@limiter.limit(read_limit)
async def availability(
    request: Request,
    org: str,
    group: str,
    locale: Locale,
    month: str | None = None,
    day: str | None = None,
) -> WidgetAvailability:
    """Return a month (``YYYY-MM``) and the free times of a day (``YYYY-MM-DD``).

    Without parameters: the month of the first free day, and that day. A day outside today …
    the booking horizon, or a malformed value, is ignored as on the standalone page.
    """
    found = _group(request)
    view = await picker(
        _booking_service(request),
        found,
        request.app.state.clock.now(),
        locale,
        f"/g/{found.slug}",
        month=month,
        day=day,
    )
    return _availability(view, locale)


def _read_booking(raw: bytes) -> WidgetBookingRequest:
    """Parse the booking body.

    Raises:
        WidgetProblem: ``invalid_request`` (422) for anything but the expected object, or
            (400) when the honeypot is filled in.
    """
    if len(raw) > MAX_BODY_BYTES:
        raise WidgetProblem("invalid_request", 413)
    try:
        body = WidgetBookingRequest.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as invalid:
        raise WidgetProblem("invalid_request", 422) from invalid
    if body.website:
        raise WidgetProblem("invalid_request", 400)
    return body


@router.post(
    "/{org}/{group}/bookings",
    response_model=WidgetBooking,
    status_code=201,
    responses={
        **_ERRORS,
        400: {"model": WidgetError, "description": "`invalid_request`: honeypot filled in."},
        409: {"model": WidgetError, "description": "`slot_unavailable`, `slot_full`, …"},
        413: {"model": WidgetError, "description": "`invalid_request`: body too large."},
        422: {"model": WidgetError, "description": "`invalid_request` or `invalid_contact`."},
    },
    summary="Book",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": WidgetBookingRequest.model_json_schema()}},
        }
    },
)
@limiter.limit(booking_limit)
async def book(request: Request, org: str, group: str, locale: Locale) -> JSONResponse:
    """Book a slot for the citizen. Needs an allowed ``Origin``.

    The answer says the appointment awaits confirmation by the link in the citizen's email
    (ADR 0056); that link, like the management link, never goes to the host page.
    """
    found = _group(request)
    body = _read_booking(await request.body())
    start = parse_start(body.start)
    if start is None:
        raise WidgetProblem("invalid_request", 422)
    receipt = await _booking_service(request).book(
        group_slug=found.slug,
        start=start,
        contact=ContactInput(body.name, body.email, body.phone or None),
        locale=locale.code,
    )
    booking = receipt.booking
    answer = WidgetBooking(
        status="booked",
        start=slot_value(booking.start),
        end=slot_value(booking.end),
        label=slot_label(booking.start, booking.end, found, locale),
        message=locale.gettext("manage.notice.pending"),  # ADR 0056: confirm by email
    )
    return JSONResponse(answer.model_dump(), status_code=201)


@router.options("/{org}/{group}/bookings", include_in_schema=False)
@limiter.limit(read_limit)
async def preflight(request: Request, org: str, group: str) -> Response:
    """Answer the browser's CORS preflight for a booking from an allowed origin."""
    if request.headers.get("access-control-request-method") != "POST":
        raise WidgetProblem("invalid_request", 400)
    return Response(
        status_code=204,
        headers={
            "access-control-allow-methods": "POST",
            "access-control-allow-headers": "content-type",
            "access-control-max-age": PREFLIGHT_MAX_AGE,
        },
    )
