"""Immediate intake for participants (ADR 0121 step 7): the form, the request page, withdrawing.

A service whose on-demand flow draws on a counted pool takes requests without an appointment at
the address of the direct call, ``/g/{slug}/direct`` (choice I11): ``direct_pages`` asks
``ImmediateRequests.offer`` first and hands the request here when there is an offer, so a link
on a client's site keeps working whatever the flow is.

    GET  /g/{slug}/direct        (via direct_pages) the form, the line now and its forecast
    POST /g/{slug}/direct        (via direct_pages) take in; set the cookie; 303 to the page
    GET  /r/{ref}                the request: waiting (place, forecast), called, done, ...
    GET  /r/{ref}/status         the same as a fragment for live-region.js; 204 while current
    GET  /r/{ref}/leave          "withdraw?": says what happens, with one form
    POST /r/{ref}/leave          withdraw; 303 to the page

The address names the request by a random reference that authorises nothing; the browser holds
``op_req`` (up to ``MAX_HELD`` entries ``<reference>.<random value>``, ``HttpOnly``,
``SameSite=Lax``), whose hashes the requests keep (choice I4, the pattern of ADR 0093). A
browser that holds an open request of the service is shown it instead of a second form (I12).
The form carries a random intake key, so a POST sent again after a lost answer gets the same
request (A16). Posts are same-origin,
rate limited like a booking and carry the honeypot; nothing personal is in an address or a log
line, and the pages show the first name at most. The page never reloads by itself (no time
limits, WCAG 2.2.1): ``static/live-region.js`` polls the fragment, a button refreshes it without
the script.
"""

import hashlib
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request
from starlette.datastructures import FormData
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.api.contact_form import HONEYPOT_FIELD, contact_input, load_contact_form
from app.api.link_session import lifetime
from app.api.participant import participant_page, templates
from app.domain.errors import DomainError, ErrorCode
from app.domain.immediate_intake import RequestStatus, new_intake_key
from app.domain.immediate_requests import ImmediateOffer, ImmediateRequests, RequestPage
from app.domain.ports import Clock
from app.domain.services import BookingService
from app.i18n.dates import format_clock
from app.web.cookies import CookieSpec, cookie_for, set_cookie
from app.web.errors import status_for
from app.web.locale import RequestLocale, request_locale
from app.web.organisation_settings import follow
from app.web.protection import booking_limit, limiter, read_limit, require_same_origin
from app.web.templates import render

router = APIRouter(include_in_schema=False)
log = structlog.get_logger()

COOKIE_BASE = "op_req"
"""The cookie of the participant's immediate request, without the ``__Host-`` prefix."""
PLAIN_PATH = "/"
"""The path over plain http: the cookie is read on ``/g/{slug}/direct`` and on ``/r/``."""
MAX_HELD = 5
"""Most immediate requests one browser holds (an abuse bound like ADR 0093's, not a setting)."""

Locale = Annotated[RequestLocale, Depends(request_locale)]
SameOrigin = Annotated[None, Depends(require_same_origin)]
Seen = Annotated[str, Query(max_length=64)]

STATUS_TEXT: dict[RequestStatus, str] = {
    RequestStatus.WAITING: "request.waiting",
    RequestStatus.CALLED: "request.called",
    RequestStatus.IN_PROGRESS: "request.in_progress",
    RequestStatus.DONE: "request.done",
    RequestStatus.WITHDRAWN: "request.withdrawn",
    RequestStatus.EXPIRED: "request.expired",
}
"""The one sentence per status: shown in the notice and announced in the status region."""
CHANGING = frozenset({RequestStatus.WAITING, RequestStatus.CALLED, RequestStatus.IN_PROGRESS})
"""Statuses that can still change into something the participant must see at once."""


def service(request: Request) -> ImmediateRequests:
    """Return immediate intake."""
    found: ImmediateRequests = request.app.state.immediate_requests
    return found


def request_cookie(request: Request) -> CookieSpec:
    """Return the cookie of the immediate request for the installation's address."""
    return cookie_for(request.app.state.settings.public_base_url, COOKIE_BASE, PLAIN_PATH)


def held(request: Request) -> dict[str, str]:
    """Return the requests the browser's cookie holds: reference to value, newest last.

    The cookie holds at most ``MAX_HELD`` entries ``<reference>.<value>`` joined by ``~``, so a
    participant who asks at two services keeps both pages (ADR 0121 step 7).
    """
    raw = request.cookies.get(request_cookie(request).name, "")
    entries = (entry.partition(".") for entry in raw.split("~")[-MAX_HELD:])
    return {ref: value for ref, _, value in entries if ref and value}


def _cookie_value(request: Request, ref: str, value: str) -> str:
    """Return the cookie with ``ref`` added as the newest entry, the oldest dropped."""
    kept = {r: v for r, v in held(request).items() if r != ref}
    kept[ref] = value
    return "~".join(f"{r}.{v}" for r, v in list(kept.items())[-MAX_HELD:])


async def open_request_of(request: Request, slug: str) -> str | None:
    """Return the reference of an open request of service ``slug`` this browser holds (I12)."""
    for ref, value in held(request).items():
        try:
            page = await service(request).page(ref, value)
        except DomainError:
            continue
        if page.group.slug == slug and page.status in CHANGING:
            return ref
    return None


async def form_page(
    request: Request,
    locale: RequestLocale,
    offer: ImmediateOffer,
    *,
    error: DomainError | None = None,
    values: dict[str, str] | None = None,
    flow: str | None = None,
) -> HTMLResponse:
    """Render the immediate form with the line now, or why it cannot be used now."""
    group = offer.flow.group
    bookings: BookingService = request.app.state.booking_service
    clock: Clock = request.app.state.clock
    contact = await load_contact_form(bookings, group, clock.now())
    errors = contact.errors(error, locale) if error else []
    fields = contact.fields(locale, errors, values or {})
    if not offer.flow.revision.collects_email:
        fields = [f for f in fields if f.name != "email"]  # A20: never ask what is not kept
    expected = offer.decision.expected_start
    context: dict[str, object] = {
        "outcome": offer.decision.outcome.value,
        "waiting": offer.waiting,
        "expected": format_clock(expected, group.tz, locale.code) if expected else None,
        "fields": fields,
        "errors": errors,
        "honeypot": HONEYPOT_FIELD,
        "intake_key": new_intake_key(),
        "flow": flow,
    }
    status = status_for(error) if error else 200
    return await participant_page(request, locale, "immediate.html", group, context, status)


async def accept(
    request: Request,
    locale: RequestLocale,
    slug: str,
    form: FormData,
    values: dict[str, str],
    offer: ImmediateOffer,
    flow: str | None,
) -> Response:
    """Take the request in; set the cookie and go to its page, or show the form again.

    ``values`` are the contact fields ``read_contact`` returned (the honeypot already checked).
    """
    key = form.get("intake_key")
    try:
        accepted = await service(request).accept(
            group_slug=slug,
            contact=contact_input(values),
            intake_key=key if isinstance(key, str) else "",
            flow=flow,
        )
    except DomainError as error:
        return await form_page(request, locale, offer, error=error, values=values, flow=flow)
    ref = accepted.request.public_ref or ""
    log.info("immediate.accepted", request_id=accepted.request.id, created=accepted.created)
    response = RedirectResponse(f"/r/{ref}", status_code=303)
    response.headers["cache-control"] = "no-store"  # it carries a cookie
    seconds = int(lifetime(request).total_seconds())
    value = _cookie_value(request, ref, accepted.cookie_value)
    set_cookie(response, request_cookie(request), value, seconds)
    return response


@dataclass(frozen=True)
class RequestView:
    """What the request page and its fragment render."""

    page: RequestPage
    ref: str

    @property
    def key(self) -> str:
        """A short digest of what is shown, so the poller can ask "still this?"."""
        shown = (self.page.status, self.page.place, self.page.expected_start)
        return hashlib.sha256(repr(shown).encode()).hexdigest()[:16]

    def context(self, request: Request, locale: RequestLocale) -> dict[str, object]:
        """Return the template context, with the poll URL while the status can still change."""
        page = self.page
        changing = page.status in CHANGING
        expected = page.expected_start
        group = page.group
        return {
            "group": group,
            "ref": self.ref,
            "status": page.status.value,
            "status_text": locale.gettext(STATUS_TEXT[page.status]),
            "place": page.place,
            "expected": format_clock(expected, group.tz, locale.code) if expected else None,
            "first_name": page.first_name,
            "refresh": changing,
            "withdrawable": page.withdrawable,
            "again": not changing,
            "poll": f"/r/{self.ref}/status?seen={self.key}" if changing else None,
            "poll_seconds": follow(request, group.organisation_id).queue_refresh_seconds,
        }


async def _view(request: Request, ref: str) -> RequestView:
    """Load the request behind ``ref`` for the browser that holds it.

    Raises:
        DomainError: ``link_required`` for anything else, the same answer every time (A31).
    """
    value = held(request).get(ref)
    if value is None:
        raise DomainError(ErrorCode.LINK_REQUIRED)
    page = await service(request).page(ref, value)
    follow(request, page.group.organisation_id)
    return RequestView(page, ref)


@router.get("/r/{ref}", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def request_page(request: Request, ref: str, locale: Locale) -> Response:
    """Show the participant's immediate request."""
    view = await _view(request, ref)
    context = view.context(request, locale)
    return await participant_page(request, locale, "request.html", view.page.group, context)


@router.get("/r/{ref}/status", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def request_status(request: Request, ref: str, locale: Locale, seen: Seen = "") -> Response:
    """Return the status fragment for the page's poller; 204 while ``seen`` is current."""
    view = await _view(request, ref)
    if view.key == seen:
        return Response(status_code=204, headers={"cache-control": "no-store"})
    return render(templates, request, "_request_status.html", locale, view.context(request, locale))


@router.get("/r/{ref}/leave", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def leave_page(request: Request, ref: str, locale: Locale) -> Response:
    """Ask whether the participant wants to withdraw, and say what that does."""
    view = await _view(request, ref)
    if not view.page.withdrawable:
        return RedirectResponse(f"/r/{ref}", status_code=303)
    context = {"group": view.page.group, "ref": ref}
    return await participant_page(request, locale, "request_leave.html", view.page.group, context)


@router.post("/r/{ref}/leave", response_class=HTMLResponse)
@limiter.limit(booking_limit)
async def leave(request: Request, ref: str, locale: Locale, _: SameOrigin) -> Response:
    """Withdraw the request (choice I7); repeating it only shows the page again."""
    value = held(request).get(ref)
    if value is None:
        raise DomainError(ErrorCode.LINK_REQUIRED)
    page = await service(request).withdraw(ref, value)
    log.info("immediate.withdrawn", request_id=page.request.id)
    return RedirectResponse(f"/r/{ref}", status_code=303)
