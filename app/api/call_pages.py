"""The participant's call page (ADR 0123): ``GET /b/m/{ref}/call``, the third kind of page.

This route is the one place that serves ``PageKind.CALL``: the response policy that lets the
page reach the call's hosts and use camera, microphone and screen sharing (the policies are
named in ``app.web.templates``). It exists only while ``embedded_call_enabled``
is on; while it is off the route answers as an address that does not exist, so nothing about the
feature shows on an installation that does not use it.

In this step the page is the shell: the appointment and one line saying the call in this page is
not available yet. It loads no script and contacts nobody; the way in is built in step 4.

    GET /b/m/{ref}/call      the call page (404 while the feature is off)
"""

from fastapi import APIRouter, Request
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse

from app.api.labels import slot_label
from app.api.link_session import authorise
from app.api.participant import participant_page
from app.domain.services import BookingService
from app.web.locale import resolve_locale
from app.web.organisation_settings import follow
from app.web.protection import limiter, read_limit
from app.web.templates import PageKind

router = APIRouter(include_in_schema=False)


@router.get("/b/m/{public_ref}/call", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def call_page(request: Request, public_ref: str) -> HTMLResponse:
    """Show the call page of the participant's booking.

    Raises:
        HTTPException: ``404`` while ``embedded_call_enabled`` is off, before anything about
            the booking is looked at.
    """
    if not request.app.state.settings.embedded_call_enabled:
        raise HTTPException(status_code=404)
    bookings: BookingService = request.app.state.booking_service
    locale = resolve_locale(request)
    details = await bookings.find(await authorise(request, public_ref))
    follow(request, details.group.organisation_id)
    booking, group = details.booking, details.group
    context = {
        "group": group,
        "ref": public_ref,
        "slot_label": slot_label(booking.start, booking.end, group, locale),
    }
    return await participant_page(request, locale, "call.html", group, context, kind=PageKind.CALL)
