"""The participant's call page (ADR 0123): ``GET /b/m/{ref}/call``, the third kind of page.

This route is the one place that serves the call page's response policy, which lets the page
reach the call's hosts and use camera, microphone and screen sharing (``app.web.security_policy``,
applied by ``participant_call_page``). It exists only while ``embedded_call_enabled`` is on and
only for a booking of a service that chose it (``join_client = embedded``, ADR 0123 step 6a;
``app.domain.call_client.uses_call_page`` is the one question every route asks). Otherwise the
route answers as an address that does not exist, so nothing about the feature shows on an
installation, or for a service, that does not use it.

Up to the click that joins (step 4b) the page works with the browser alone: the start state
(the appointment and one button), the device check and what a refused permission needs. It loads
one script and one stylesheet of ours, asks the browser for the camera and the microphone only
after the button of the start state, and contacts nobody else.

The click that joins (step 4c) asks ``POST /b/m/{ref}/call/token``, loads the vendored video SDK
and connects. The server decides whether the page may do that at all: the markup names the SDK
file, with its checksum as subresource integrity, only when the file is placed and its bytes
equal the manifest's checksum (``app.adapters.vendored_files``). Otherwise the join button
leads to the state that says the call is not available in this page, with what
``embedded_call_webex_option`` and the organisation's contact details allow (ADR 0123 point 11).
The markup also carries the two connection values (``embedded_call_connect_seconds`` and
``embedded_call_connect_attempts``); the script reads them from there and has no number of its own.

    GET  /b/m/{ref}/call         the call page (404 while the feature is off or for another
                                 service's booking)
    POST /b/m/{ref}/call/token   the guest token for the click that joins (ADR 0123 point 6)
    GET  /b/m/{ref}/call/staff   who the participant talks to (ADR 0123 step 5c)
    GET/POST /b/m/{ref}/call/chat  the chat beside the call (step 7c, ``app.api.call_chat_pages``;
                                 the page carries its address only for a booking that has a chat)

The token endpoint (step 4a) answers JSON with ``token``, ``expires_at`` (UTC, ISO 8601) and
``link`` (the meeting's guest link, never the host's). Every answer the endpoint builds carries
``Cache-Control: no-store``; the 403 and 404 pages are the site's own, as for every page under the
link. It needs the participant's session for the booking and the form protection of every
participant post, answers only inside the join window of ``/join/go`` (``join_state``), and is
limited per address (the common read limit) and per booking, counted for an open booking only
(``embedded_call_token_per_booking_per_hour``). The token and the links are never stored, logged,
put in a URL, an event or a job. Answers:

    200  the token
    403  no session for this booking, or a cross-site post (a booking that does not exist or is
         not theirs answers alike)
    404  the switch is off, or the booking's service did not choose the call page (an address
         that does not exist)
    409  ``{"error": "not_open"}``: not confirmed, not in the window, or no meeting
    429  ``{"error": "rate_limited"}`` with ``Retry-After``, from either limit
    503  ``{"error": "unavailable"}``: the platform refused or cannot be reached


The staff endpoint (step 5c) answers ``{"name": "..."}`` or ``{}`` and nothing else, under the same
session and the same ``no-store``, and under two limits of its own, both derived from the page's
poll rate: a bound per address before any session is checked (``call_staff_address_limit``, ten
times the per-booking rate, so people behind one address do not starve each other) and a bound per
booking that counts only a request holding the participant's session (``within_call_staff_limit``,
three times the poll rate; knowing the reference is not enough to use it up). It is the one place
the page learns the name
of the staff member, from our own record of who accepted the booking and never from the video
platform. What the name is follows the organisation's ``embedded_call_staff_name`` (the screen name
by default, ``app.domain.screen_name``). It answers ``{}`` while nobody has accepted the booking,
for the setting ``term``, for a staff member without a screen name and after the booking's purge
(which deletes the claim); the staff table is never scanned per request
(``app.domain.staff_screen_names``).

Order of the refusals, so that an outsider learns nothing about a service: the switch first
(before anything is looked up, the same answer for every address), then the participant's session
for the booking (an outsider gets the site's usual refusal whatever the booking's service is), and
only then the service's choice, which only the participant themself can run into. The switch is
the kill switch: off means nobody, whatever a service says.
"""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.adapters.vendored_files import VendoredFile
from app.api.call_guards import require_service, require_switch, staff_name_json
from app.api.labels import slot_label
from app.api.link_session import authorise
from app.api.participant import participant_call_page
from app.config import Settings
from app.domain.call_chat_service import CallChatService
from app.domain.call_client import other_way_offer
from app.domain.call_page import shows_other_way
from app.domain.call_token_service import (
    REFUSAL_STATUS,
    CallToken,
    CallTokenService,
    TokenRefusal,
)
from app.domain.guest_access import GUEST_TERM_KEY, DisplayNameMode
from app.domain.participant_links import PublicRef
from app.domain.privacy_statement import privacy_link
from app.domain.scheduling import Group
from app.domain.services import BookingDetails, BookingService
from app.domain.trial_call_service import TrialCallService, TrialView
from app.i18n.dates import format_clock
from app.web.contact_labels import contact_service, contact_view
from app.web.error_pages import refuse_rate_limited
from app.web.locale import RequestLocale, resolve_locale
from app.web.organisation_settings import follow
from app.web.protection import (
    call_staff_address_limit,
    limiter,
    read_limit,
    require_same_origin,
    within_call_staff_limit,
    within_call_token_limit,
)
from app.web.static_assets import ASSETS

router = APIRouter(include_in_schema=False)

SameOrigin = Annotated[None, Depends(require_same_origin)]

NO_STORE = {"cache-control": "no-store"}
"""A token, or the answer that there is none, is never kept by a browser or a proxy."""


async def _page_context(
    request: Request,
    locale: RequestLocale,
    current: Settings,
    group: Group,
    public_ref: str,
    *,
    slot: str,
    token_url: str,
    staff_url: str,
    chat_url: str = "",
    other_way_href: str,
    back_href: str,
    back_text: str = "join.back",
    trial_until: str | None = None,
) -> dict[str, object]:
    """Return what ``call.html`` needs for the appointment's page or the trial call's page.

    The two pages are the same page (ADR 0123 point 9); only the addresses differ: the token and
    the staff address (none for a trial call: nobody is waiting in it), where "join another way"
    and "back" lead (``back_text`` is the catalogue key of the back link's words, which must
    say where it leads), and ``trial_until`` (when the trial call closes), which turns the start
    state's line about the time into the trial's. Everything the browser may see is decided here.
    """
    offer = other_way_offer(group, current.embedded_call_webex_option)
    sdk: VendoredFile = request.app.state.call_sdk
    sdk_file = sdk.offer()
    sdk_src = ""
    if sdk_file is not None:
        try:
            sdk_src = ASSETS.url(sdk_file.path)
        except FileNotFoundError:
            sdk_file = None  # gone between the check and the address: no offer, not an error
    return {
        "group": group,
        "ref": public_ref,
        "slot_label": slot,
        "trial_until": trial_until,
        # The address a participant sees in their address bar is the installation's public one
        # (ADR 0086 point 4: never an address read from the request).
        "host": urlsplit(current.public_base_url).hostname or "",
        "contacts": contact_view(await contact_service(request).public_for_group(group), locale),
        # The server decides what the browser may see: no link at all unless the setting allows.
        "other_way_href": other_way_href,
        "back_href": back_href,
        "back_text": back_text,
        "other_way_before": shows_other_way(offer, failed=False),
        "other_way_failed": shows_other_way(offer, failed=True),
        # The organisation's privacy statement, linked from the line about the external service
        # (point 5): only an https address (``privacy_link`` checks it again where it is shown).
        "privacy_url": privacy_link(current.privacy_statement_url) or "",
        # The server decides whether the page may load the SDK at all: a file that is not
        # placed, or whose bytes are not the manifest's, is never named in the markup.
        "sdk_src": sdk_src,
        "sdk_integrity": sdk_file.integrity if sdk_file else "",
        "token_url": token_url,
        "staff_url": staff_url,
        # The chat beside the call (step 7c): the address only for a booking that has one, so any
        # other booking's page has no side bar, no poll of it and no sentence about it.
        "chat_url": chat_url,
        "chat_max_chars": current.embedded_call_chat_max_chars,
        "poll_seconds": current.embedded_call_chat_poll_seconds,
        "connect_seconds": current.embedded_call_connect_seconds,
        "connect_attempts": current.embedded_call_connect_attempts,
    }


@router.get("/b/m/{public_ref}/call", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def call_page(request: Request, public_ref: str) -> HTMLResponse:
    """Show the call page of the participant's booking.

    Raises:
        HTTPException: ``404`` while ``embedded_call_enabled`` is off, before anything about
            the booking is looked at, and for a booking of a service that did not choose it.
    """
    require_switch(request)
    bookings: BookingService = request.app.state.booking_service
    locale = resolve_locale(request)
    details = await bookings.find(await authorise(request, public_ref))
    require_service(request, details)
    current = follow(request, details.group.organisation_id)
    booking, group = details.booking, details.group
    context = await _page_context(
        request,
        locale,
        current,
        group,
        public_ref,
        slot=slot_label(booking.start, booking.end, group, locale),
        token_url=f"/b/m/{public_ref}/call/token",
        staff_url=f"/b/m/{public_ref}/call/staff",
        chat_url=_chat_url(request, details, public_ref),
        other_way_href=f"/b/m/{public_ref}/join/go",
        back_href=f"/b/m/{public_ref}/join",
    )
    return await participant_call_page(request, locale, "call.html", group, context)


@router.post("/b/m/{public_ref}/call/token")
@limiter.limit(read_limit)
async def call_token(request: Request, public_ref: str, _: SameOrigin) -> Response:
    """Hand the call page a guest token for the participant's click that joins.

    Only the participant's session for this booking may ask; whether the booking exists or
    belongs to someone else is not told (the same not-found answer as every page under the
    link). Nothing of the booking goes to the video platform but the guest's subject and name.

    Raises:
        HTTPException: ``404`` while ``embedded_call_enabled`` is off, before anything about
            the booking is looked at, and for a booking of a service that did not choose it.
    """
    require_switch(request)
    ref = await authorise(request, public_ref)
    bookings: BookingService = request.app.state.booking_service
    details = await bookings.find(ref)  # the one read: the service takes the booking from here
    require_service(request, details)
    current = follow(request, details.group.organisation_id)
    service: CallTokenService = request.app.state.call_token_service
    outcome = await service.issue(
        details.booking,
        DisplayNameMode(current.embedded_call_display_name),
        _guest_term(request, details.group),
        lambda: within_call_token_limit(public_ref),
    )
    return await _token_response(request, outcome)


def _chat_url(request: Request, details: BookingDetails, public_ref: str) -> str:
    """Return the address of the booking's chat, or nothing when the booking has none."""
    chat: CallChatService = request.app.state.call_chat_service
    return f"/b/m/{public_ref}/call/chat" if chat.has_chat(details.booking, details.group) else ""


def _guest_term(request: Request, group: Group) -> str:
    """Return the organisation's word for a participant, in the reader's language."""
    return (
        resolve_locale(request).for_scope(group.organisation_id, group.id).gettext(GUEST_TERM_KEY)
    )


async def _token_response(request: Request, outcome: CallToken | TokenRefusal) -> Response:
    """Answer a token request: the token, or the refusal with its status (the one table)."""
    if outcome is TokenRefusal.RATE_LIMITED:
        return await refuse_rate_limited(request)
    if isinstance(outcome, TokenRefusal):
        return JSONResponse(
            {"error": outcome.value}, status_code=REFUSAL_STATUS[outcome], headers=NO_STORE
        )
    return _answer(outcome)


@router.get("/b/m/{public_ref}/call/staff")
@limiter.limit(call_staff_address_limit)
async def call_staff(request: Request, public_ref: str) -> Response:
    """Tell the call page who the participant talks to: ``{"name": ...}`` or ``{}``.

    Only the participant's session for this booking may ask; the answer is a name or nothing
    (``app.domain.screen_name.staff_label``), never an identifier of the staff member.

    Raises:
        HTTPException: ``404`` while ``embedded_call_enabled`` is off, before anything about
            the booking is looked at, and for a booking of a service that did not choose it.
    """
    require_switch(request)
    ref = await authorise(request, public_ref)
    if not within_call_staff_limit(public_ref):  # only a request with the session is counted
        return await refuse_rate_limited(request)
    bookings: BookingService = request.app.state.booking_service
    details = await bookings.find(ref)
    require_service(request, details)
    return JSONResponse(await staff_name_json(request, details), headers=NO_STORE)


async def _trial_view(request: Request, public_ref: str) -> tuple[TrialView, PublicRef]:
    """Return the view of the participant's trial call for a service that uses the page.

    The same order of refusals as the appointment's routes: the switch, then the participant's
    session, then the service's choice.

    Raises:
        HTTPException: ``404`` while the switch is off or for a service that did not choose the
            page.
    """
    require_switch(request)
    ref = await authorise(request, public_ref)
    trials: TrialCallService = request.app.state.trial_call_service
    view = await trials.view(ref)
    if not view.call_page:
        raise HTTPException(status_code=404)
    return view, ref


@router.get("/b/m/{public_ref}/test/call", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def trial_call_page(request: Request, public_ref: str) -> Response:
    """Show the call page on the participant's open trial call (ADR 0123 point 9).

    Raises:
        HTTPException: ``404`` while ``embedded_call_enabled`` is off and for a booking of a
            service that did not choose the page. A trial call that is not open sends the
            participant back to the trial page (``303``).
    """
    _, ref = await _trial_view(request, public_ref)
    trials: TrialCallService = request.app.state.trial_call_service
    view = await trials.enter_page(ref)
    if view is None:
        return RedirectResponse(f"/b/m/{public_ref}/test", status_code=303)
    locale = resolve_locale(request)
    current = follow(request, view.group.organisation_id)
    group = view.group
    until = format_clock(view.expires_at, group.tz, locale.code) if view.expires_at else ""
    context = await _page_context(
        request,
        locale,
        current,
        group,
        public_ref,
        slot=until,
        token_url=f"/b/m/{public_ref}/test/call/token",
        staff_url="",  # nobody waits in a trial call, so there is nobody to name
        other_way_href=f"/b/m/{public_ref}/test/go",
        back_href=f"/b/m/{public_ref}/test",
        back_text="trial.page.back",
        trial_until=until,
    )
    return await participant_call_page(request, locale, "call.html", group, context)


@router.post("/b/m/{public_ref}/test/call/token")
@limiter.limit(read_limit)
async def trial_call_token(request: Request, public_ref: str, _: SameOrigin) -> Response:
    """Hand the call page a guest token for the open trial call (ADR 0123 point 9).

    As ``call_token``: the participant's session, the form protection, the same budget per
    booking and the same guest; only the meeting is the trial call's, and only while it is open.

    Raises:
        HTTPException: ``404`` while ``embedded_call_enabled`` is off and for a booking of a
            service that did not choose the page.
    """
    view, _ref = await _trial_view(request, public_ref)
    current = follow(request, view.group.organisation_id)
    service: CallTokenService = request.app.state.call_token_service
    outcome = await service.issue_trial(
        view.booking,
        view.group,
        DisplayNameMode(current.embedded_call_display_name),
        _guest_term(request, view.group),
        lambda: within_call_token_limit(public_ref),
    )
    return await _token_response(request, outcome)


def _answer(token: CallToken) -> JSONResponse:
    """Return the token's answer; the one place a token leaves the server."""
    return JSONResponse(
        {
            "token": token.access.token,
            "expires_at": token.access.expires_at.isoformat(),
            "link": token.link,
        },
        headers=NO_STORE,
    )
