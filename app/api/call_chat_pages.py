"""The chat beside the participant's call (ADR 0123 point 8, step 7c): its read and its send.

    GET  /b/m/{ref}/call/chat?since=&waiting=   what is new in the chat, the state of the messages
                                                the page still shows as waiting, who the staff
                                                member is called and whether the chat is closed
    POST /b/m/{ref}/call/chat                   a message of the participant: ``{"id", "text"}``

The routes sit next to the call page's other two (``app.api.call_pages``) and ask in the same
order, so an outsider learns nothing: the platform switch (404 before anything is looked up), the
participant's session for the booking (the site's usual refusal), the booking's service (404 unless
it uses the page) and then whether the booking has a chat at all (404 otherwise, as an address that
does not exist). A write also needs the same-origin protection of every participant post. Every
answer carries ``Cache-Control: no-store``.

The read answers ``{"messages": [...], "states": {...}, "staff": "...", "closed": null|"ended"|
"staff"}``. A message is ``{"id", "author", "text", "state", "time"}``; only messages newer than
``since`` are in it, so a read that brings nothing carries none, and ``states`` holds the new state
(``delivered`` or ``refused``) of the ids named in ``waiting`` only. ``staff`` is the staff member's
name for the participant by the organisation's ``embedded_call_staff_name`` (the screen name by
default, never an account name unless the organisation chose that; ``""`` for none). It is limited
per booking as the staff poll is (the same budget, only a request with the session counts).

The send answers ``{"message": {...}}`` and otherwise ``{"error": code}`` with the status of
``REFUSAL_STATUS``: 400 ``empty``, ``too_long`` or ``bad_request``, 409 ``closed`` or ``full``,
429 ``rate_limited``, 503 ``unavailable`` (the contact center could not be reached: nothing was
kept, the page keeps the text). The text of a message is personal data: it is read here from the
request body and handed on, never put in a log, an event, an error or an address.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from app.api.link_session import authorise
from app.domain.call_chat import CallMessage
from app.domain.call_chat_service import CallChatService, ChatRead, SendRefusal
from app.domain.call_client import uses_call_page
from app.domain.guest_access import StaffNameMode
from app.domain.participant_links import PublicRef
from app.domain.scheduling import Group
from app.domain.services import BookingDetails, BookingService
from app.domain.staff_screen_names import StaffScreenNames
from app.i18n.dates import format_clock
from app.web.error_pages import refuse_rate_limited
from app.web.locale import resolve_locale
from app.web.organisation_settings import follow
from app.web.protection import (
    call_staff_address_limit,
    limiter,
    read_limit,
    require_same_origin,
    within_call_chat_limit,
)

router = APIRouter(include_in_schema=False)

SameOrigin = Annotated[None, Depends(require_same_origin)]

NO_STORE = {"cache-control": "no-store"}
"""A chat is personal data and never kept by a browser or a proxy."""

BODY_LIMIT = 32 * 1024
"""The most bytes of a send's body read: the longest message (4,000 characters, the top of
``embedded_call_chat_max_chars``) at four bytes each, and room for the JSON around it. A fixed
protection bound, not a setting."""

REFUSAL_STATUS: dict[SendRefusal, int] = {
    SendRefusal.EMPTY: 400,
    SendRefusal.TOO_LONG: 400,
    SendRefusal.BAD_REQUEST: 400,
    SendRefusal.CLOSED: 409,
    SendRefusal.FULL: 409,
    SendRefusal.UNAVAILABLE: 503,
    SendRefusal.NO_CHAT: 404,
}
"""The HTTP status of each refusal, the one table the send answers from (429 is the limiter's)."""

WAITING_QUERY_CAP = 64
"""The most characters of the ``waiting`` query read (a list of ids, twenty at the most)."""


def _line(message: CallMessage, group: Group, code: str) -> dict[str, object]:
    """Return one message as the page reads it; the time is worded here, in the group's zone."""
    return {
        "id": message.id,
        "author": message.author.value,
        "text": message.message_text,
        "state": message.state.value,
        "time": format_clock(message.at, group.tz, code),
    }


def _ids(query: str) -> list[int]:
    """Return the numbers of a comma-separated query value; anything else is left out."""
    found = [int(part) for part in query[:WAITING_QUERY_CAP].split(",") if part.strip().isdigit()]
    return [n for n in found if n < 2**62]


@router.get("/b/m/{public_ref}/call/chat")
@limiter.limit(call_staff_address_limit)
async def chat_read(
    request: Request, public_ref: str, since: int = 0, waiting: str = ""
) -> Response:
    """Tell the call page what is new in its chat.

    Raises:
        HTTPException: ``404`` as the call routes do, and for a booking without a chat.
    """
    if not request.app.state.settings.embedded_call_enabled:
        raise HTTPException(status_code=404)
    ref = await authorise(request, public_ref)
    if not within_call_chat_limit(public_ref):  # only a request with the session is counted
        return await refuse_rate_limited(request)
    details, group = await _booking_of(request, ref)
    service: CallChatService = request.app.state.call_chat_service
    read: ChatRead | None = await service.read(details.booking, group, max(since, 0), _ids(waiting))
    if read is None:
        raise HTTPException(status_code=404)
    current = follow(request, group.organisation_id)
    names: StaffScreenNames = request.app.state.staff_names
    claim = details.booking.claim
    label = await names.label_of(
        claim.claimed_by if claim else None, StaffNameMode(current.embedded_call_staff_name)
    )
    code = resolve_locale(request).code
    return JSONResponse(
        {
            "messages": [_line(m, group, code) for m in read.messages],
            "states": {str(i): state.value for i, state in read.states.items()},
            "staff": label.as_json().get("name", ""),
            "closed": read.closed.value if read.closed else None,
        },
        headers=NO_STORE,
    )


async def _booking_of(request: Request, ref: PublicRef) -> tuple[BookingDetails, Group]:
    """Return the booking of a checked session after the service's and the chat's refusals.

    The order of the call routes: the participant's session is checked before (``authorise``),
    then the booking's service (404 unless it uses the page), then the chat itself (404 for a
    booking without one).

    Raises:
        HTTPException: ``404`` for a service that did not choose the page, or without a chat.
    """
    bookings: BookingService = request.app.state.booking_service
    details = await bookings.find(ref)
    enabled = request.app.state.settings.embedded_call_enabled
    chat: CallChatService = request.app.state.call_chat_service
    if not uses_call_page(details.group, platform_enabled=enabled) or not chat.has_chat(
        details.booking, details.group
    ):
        raise HTTPException(status_code=404)
    return details, details.group


async def _body(request: Request) -> dict[str, object] | None:
    """Return the send's JSON body as an object, or ``None`` when it is not one or is too big."""
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return None
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > BODY_LIMIT:
            return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


@router.post("/b/m/{public_ref}/call/chat")
@limiter.limit(read_limit)
async def chat_send(request: Request, public_ref: str, _: SameOrigin) -> Response:
    """Send the participant's message to the staff member through the contact center.

    Raises:
        HTTPException: ``404`` as the call routes do, and for a booking without a chat.
    """
    if not request.app.state.settings.embedded_call_enabled:
        raise HTTPException(status_code=404)
    ref = await authorise(request, public_ref)
    details, group = await _booking_of(request, ref)
    body = await _body(request)
    text = body.get("text") if body else None
    client_id = body.get("id") if body else None
    if not isinstance(text, str) or not isinstance(client_id, str):
        return _refused(SendRefusal.BAD_REQUEST)
    current = follow(request, group.organisation_id)
    service: CallChatService = request.app.state.call_chat_service
    outcome = await service.send(
        details.booking,
        group,
        text,
        client_id,
        max_chars=current.embedded_call_chat_max_chars,
        per_minute=current.embedded_call_chat_per_minute,
    )
    if outcome is SendRefusal.RATE_LIMITED:
        return await refuse_rate_limited(request)
    if isinstance(outcome, SendRefusal):
        return _refused(outcome)
    return JSONResponse(
        {"message": _line(outcome, group, resolve_locale(request).code)}, headers=NO_STORE
    )


def _refused(refusal: SendRefusal) -> JSONResponse:
    """Return the answer to a message that was not sent."""
    return JSONResponse(
        {"error": refusal.value}, status_code=REFUSAL_STATUS[refusal], headers=NO_STORE
    )
