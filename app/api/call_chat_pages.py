"""The chat beside the participant's call (ADR 0123 point 8, step 7c): its read and its send.

    GET  /b/m/{ref}/call/chat?since=&waiting=   what is new in the chat, the state of the messages
                                                the page still shows as waiting, who the staff
                                                member is called and whether the chat is closed
    POST /b/m/{ref}/call/chat                   a message of the participant: ``{"id", "text"}``

The routes sit next to the call page's other two (``app.api.call_pages``) and ask in the same
order (``app.api.call_guards``), so an outsider learns nothing: the platform switch (404 before
anything is looked up), the participant's session for the booking (the site's usual refusal) and
then whether the booking's service uses the page and the booking has a chat (404 otherwise, as an
address that does not exist). A write also needs the same-origin protection of every participant
post. Every answer carries ``Cache-Control: no-store``.

The read answers ``{"messages": [...], "states": {...}, "staff": "...", "closed": null|"ended"|
"staff"|"lost", "more": false}``. A message is ``{"id", "author", "text", "state", "time", "at"}``
(``time`` is worded in the group's zone, with the date when it is not today); only messages newer
than ``since`` are in it, at most ``READ_LIMIT`` (``more`` says there are further ones), so a read
that brings nothing carries none, and ``states`` holds the new state (``delivered`` or ``refused``)
of the ids named in ``waiting`` only. ``staff`` is the staff member's name for the participant by
the organisation's ``embedded_call_staff_name`` (``app.api.call_guards.staff_name_json``; ``""``
for none). It is limited per booking as the staff poll is (the same budget, only a request with
the session counts).

The send answers ``{"message": {...}}`` and otherwise ``{"error": code}`` with the status of
``REFUSAL_STATUS``: 400 ``empty``, ``too_long`` or ``bad_request``, 409 ``closed`` or ``full``,
429 ``rate_limited`` (the messages of the last minute, or the attempts of the booking: a platform
that is down is not asked without end), 503 ``unavailable`` (the contact center did not take it:
nothing was kept, the page keeps the text). The text of a message is personal data: it is read
here from the request body and handed on, never put in a log, an event, an error or an address.
"""

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from app.api.call_guards import require_switch, staff_name_json
from app.api.link_session import authorise
from app.domain.call_chat import CallMessage, ChatRefusal
from app.domain.call_chat_service import WAITING_CAP, CallChatService
from app.domain.participant_links import PublicRef
from app.domain.ports import Clock
from app.domain.scheduling import Group
from app.domain.services import BookingDetails, BookingService
from app.i18n.dates import format_clock, format_day_compact
from app.web.error_pages import refuse_rate_limited
from app.web.locale import resolve_locale
from app.web.organisation_settings import follow
from app.web.protection import (
    call_staff_address_limit,
    limiter,
    read_limit,
    require_same_origin,
    within_call_chat_limit,
    within_call_chat_send_limit,
)

router = APIRouter(include_in_schema=False)

SameOrigin = Annotated[None, Depends(require_same_origin)]

NO_STORE = {"cache-control": "no-store"}
"""A chat is personal data and never kept by a browser or a proxy."""

BODY_LIMIT = 32 * 1024
"""The most bytes of a send's body read: the longest message (4,000 characters, the top of
``embedded_call_chat_max_chars``) at four bytes each, and room for the JSON around it. A fixed
protection bound, not a setting."""

MAX_ID = 2**62
"""Message ids are database row numbers; a larger number in a query is not one."""

WAITING_QUERY_CAP = WAITING_CAP * (len(str(MAX_ID)) + 1)
"""The most characters of the ``waiting`` query read: ``WAITING_CAP`` ids of the longest length and
a comma each. A fixed rule derived from those two, not a setting."""

REFUSAL_STATUS: dict[ChatRefusal, int] = {
    ChatRefusal.EMPTY: 400,
    ChatRefusal.TOO_LONG: 400,
    ChatRefusal.BAD_REQUEST: 400,
    ChatRefusal.CLOSED: 409,
    ChatRefusal.FULL: 409,
    ChatRefusal.UNAVAILABLE: 503,
    ChatRefusal.NO_CHAT: 404,
}
"""The HTTP status of each refusal, the one table the send answers from (429 is the limiter's)."""


def _line(message: CallMessage, group: Group, code: str, now: datetime) -> dict[str, object]:
    """Return one message as the page reads it; the time is worded in the group's zone.

    A message that is not from today (in that zone) carries its date in front of the time.
    """
    when = message.at.astimezone(group.tz)
    label = format_clock(message.at, group.tz, code)
    if when.date() != now.astimezone(group.tz).date():
        label = f"{format_day_compact(when.date(), code)} {label}"
    return {
        "id": message.id,
        "author": message.author.value,
        "text": message.message_text,
        "state": message.state.value,
        "time": label,
        "at": message.at.isoformat(),
    }


def _ids(query: str) -> list[int]:
    """Return the numbers of a comma-separated query value; anything else is left out.

    Only ASCII digits count (``str.isdigit`` also accepts superscripts, which ``int`` refuses).
    """
    parts = (part.strip() for part in query[:WAITING_QUERY_CAP].split(","))
    found = [int(part) for part in parts if part.isascii() and part.isdecimal()]
    return [n for n in found if n < MAX_ID][:WAITING_CAP]


async def _booking_of(request: Request, ref: PublicRef) -> BookingDetails:
    """Return the booking of a checked session after the service's and the chat's refusals.

    The order of the call routes: the participant's session is checked before (``authorise``),
    then the booking's service and the chat itself (``has_chat`` holds both: a conversation task
    on a service that uses the page), a 404 for either.

    Raises:
        HTTPException: ``404`` for a service that did not choose the page, or without a chat.
    """
    bookings: BookingService = request.app.state.booking_service
    details = await bookings.find(ref)
    chat: CallChatService = request.app.state.call_chat_service
    if not chat.has_chat(details.booking, details.group):
        raise HTTPException(status_code=404)
    return details


@router.get("/b/m/{public_ref}/call/chat")
@limiter.limit(call_staff_address_limit)
async def chat_read(
    request: Request, public_ref: str, since: int = 0, waiting: str = ""
) -> Response:
    """Tell the call page what is new in its chat.

    Raises:
        HTTPException: ``404`` as the call routes do, and for a booking without a chat.
    """
    require_switch(request)
    ref = await authorise(request, public_ref)
    if not within_call_chat_limit(public_ref):  # only a request with the session is counted
        return await refuse_rate_limited(request)
    details = await _booking_of(request, ref)
    group = details.group
    service: CallChatService = request.app.state.call_chat_service
    read = await service.read(details.booking, max(since, 0), _ids(waiting))
    clock: Clock = request.app.state.clock
    code = resolve_locale(request).code
    now = clock.now()
    return JSONResponse(
        {
            "messages": [_line(m, group, code, now) for m in read.messages],
            "states": {str(i): state.value for i, state in read.states.items()},
            "staff": (await staff_name_json(request, details)).get("name", ""),
            "closed": read.closed.value if read.closed else None,
            "more": read.more,
        },
        headers=NO_STORE,
    )


async def _body(request: Request) -> dict[str, object] | None:
    """Return the send's JSON body as an object, or ``None`` when it is not one or is too big.

    Deeply nested JSON raises ``RecursionError``, not ``ValueError``: both are a body the page
    would not make.
    """
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return None
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > BODY_LIMIT:
            return None
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return body if isinstance(body, dict) else None


@router.post("/b/m/{public_ref}/call/chat")
@limiter.limit(read_limit)
async def chat_send(request: Request, public_ref: str, _: SameOrigin) -> Response:
    """Send the participant's message to the staff member through the contact center.

    Raises:
        HTTPException: ``404`` as the call routes do, and for a booking without a chat.
    """
    require_switch(request)
    ref = await authorise(request, public_ref)
    details = await _booking_of(request, ref)
    if not within_call_chat_send_limit(public_ref):  # the attempts, also those that were taken back
        return await refuse_rate_limited(request)
    group = details.group
    body = await _body(request)
    text = body.get("text") if body else None
    client_id = body.get("id") if body else None
    if not isinstance(text, str) or not isinstance(client_id, str):
        return _refused(ChatRefusal.BAD_REQUEST)
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
    if outcome is ChatRefusal.RATE_LIMITED:
        return await refuse_rate_limited(request)
    if isinstance(outcome, ChatRefusal):
        return _refused(outcome)
    clock: Clock = request.app.state.clock
    line = _line(outcome, group, resolve_locale(request).code, clock.now())
    return JSONResponse({"message": line}, headers=NO_STORE)


def _refused(refusal: ChatRefusal) -> JSONResponse:
    """Return the answer to a message that was not sent."""
    return JSONResponse(
        {"error": refusal.value}, status_code=REFUSAL_STATUS[refusal], headers=NO_STORE
    )
