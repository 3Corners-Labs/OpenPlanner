"""The citizen's trial call on Webex (ADR 0071): ``/b/{token}/test``.

Citizens, often first-timers, can try camera, microphone, speakers and browser in a real Webex
meeting on the group's site, well before the appointment. The page offers a button that opens
the meeting; the meeting stays open for a few minutes and nobody from the team is waiting in
it. Like the join page it never reloads by itself (WCAG 2.2.1).

    GET  /b/{token}/test       status page: what the trial call is, the button, or why not
    POST /b/{token}/test       open a trial call ─► 303 back to the status page
    GET  /b/{token}/test/go    record the click, 303 to the trial meeting (else back)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.api.citizen import citizen_page
from app.api.labels import slot_label
from app.domain.trial_call_service import TrialCallService, TrialView
from app.i18n.dates import format_clock
from app.web.locale import RequestLocale, request_locale
from app.web.organisation_settings import follow
from app.web.protection import limiter, read_limit, require_same_origin

router = APIRouter(include_in_schema=False)

Locale = Annotated[RequestLocale, Depends(request_locale)]
SameOrigin = Annotated[None, Depends(require_same_origin)]


def _service(request: Request) -> TrialCallService:
    """Return the trial call service."""
    service: TrialCallService = request.app.state.trial_call_service
    return service


def _context(view: TrialView, token: str, locale: RequestLocale) -> dict[str, object]:
    """Context of the trial call page: names, times and the state, never contact details."""
    group, booking = view.group, view.booking
    return {
        "token": token,
        "state": view.state.value,
        "failed": view.failed,
        "calls_left": view.calls_left,
        "minutes": group.trial_call_minutes,
        "closes_at": (
            format_clock(view.expires_at, group.tz, locale.code) if view.expires_at else None
        ),
        "slot_label": slot_label(booking.start, booking.end, group, locale),
    }


@router.get("/b/{token}/test", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def trial_page(request: Request, token: str, locale: Locale) -> HTMLResponse:
    """Show what the trial call is and the button that opens it."""
    view = await _service(request).view(token)
    follow(request, view.group.organisation_id)
    return await citizen_page(
        request, locale, "trial.html", view.group, _context(view, token, locale)
    )


@router.post("/b/{token}/test", response_class=HTMLResponse)
@limiter.limit(read_limit)
async def open_trial(request: Request, token: str, locale: Locale, _: SameOrigin) -> Response:
    """Open a trial call. A refusal by Webex is shown on the page; it does not count."""
    view = await _service(request).open(token)
    follow(request, view.group.organisation_id)
    if view.failed:
        return await citizen_page(
            request, locale, "trial.html", view.group, _context(view, token, locale), 503
        )
    return RedirectResponse(f"/b/{token}/test", status_code=303)


@router.get("/b/{token}/test/go")
@limiter.limit(read_limit)
async def trial_go(request: Request, token: str) -> Response:
    """Send the citizen into the open trial meeting, or back to the page when there is none."""
    link = await _service(request).join_link(token)
    return RedirectResponse(link or f"/b/{token}/test", status_code=303)
