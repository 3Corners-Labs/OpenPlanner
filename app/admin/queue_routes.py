"""The own queue of a group (ADR 0043): agents see waiting bookings and pick one up.

Every route needs ``queue.work`` on the group; a group that dispatches to the contact center
has no queue (404). The list shows the first name only. The meeting link appears only on the
page of a booking the agent claimed. A row the agent lacks a skill for stays in its place with
the missing skills named and no *Claim* button (ADR 0121, choice S3); a row the agent holds but
no longer qualifies for offers *Release* instead of *Open*. The list and the claimed booking
refresh themselves through a fragment that ``static/live-region.js`` polls (ADR 0072); without
the script the "Refresh" link reloads, and new and waiting rows are announced in a polite live
region.

    GET  /admin/groups/{slug}/queue                         queue.work
    GET  /admin/groups/{slug}/queue/live?seen=KEY           queue.work  (fragment, 204 if same)
    POST /admin/groups/{slug}/queue/next                    queue.work  (pick up next)
    POST /admin/groups/{slug}/queue/{booking_id}/claim      queue.work  (free pick only)
    GET  /admin/groups/{slug}/queue/{booking_id}            queue.work  (claimer only)
    GET  /admin/groups/{slug}/queue/{booking_id}/live       queue.work  (fragment, claimer only)
    POST /admin/groups/{slug}/queue/{booking_id}/release    queue.work  (claimer, group admin)
    POST /admin/groups/{slug}/queue/{booking_id}/start      queue.work  (claimer; request work)
    POST /admin/groups/{slug}/queue/{booking_id}/complete   queue.work  (claimer; a group admin
                                                                         for work in recovery)
    POST /admin/groups/{slug}/queue/{booking_id}/check-in   queue.work  (ADR 0121 step 7)

Immediate work without an appointment (ADR 0121 step 7) is listed under the appointments; its
routes are ``request_queue_routes``.

Request work (ADR 0121 step 5): a booking of a flow without video on a counted pool has no
meeting; its page offers *Start* and then *Complete* instead of the meeting link.
"""

from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import tzinfo
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission, can_function
from app.admin.forms import raise_refusal, redirect_done
from app.admin.group_routes import group_page
from app.admin.live import Seen, fragment, key
from app.admin.pages import Locale, SameOrigin, admin_moment
from app.admin.request_queue_views import (
    ImmediateRowView,
    WorkView,
    immediate_rows,
    work_view,
)
from app.admin.skill_inputs import skill_name
from app.domain.authorization import Permission
from app.domain.errors import DomainError, ErrorCode
from app.domain.fallback import FallbackService
from app.domain.own_queue import ParticipantState, VideoState
from app.domain.queue_service import ClaimedBooking, QueueRow, QueueService, QueueView
from app.domain.routing import GroupRouting
from app.i18n import BoundGettext
from app.i18n.dates import format_clock
from app.web.errors import ERROR_KEYS
from app.web.locale import RequestLocale
from app.web.organisation_settings import settings_for
from app.web.routing_labels import routing_summary
from app.web.templates import FragmentResponse

router = APIRouter(prefix="/admin", include_in_schema=False)

WorkQueue = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.QUEUE_WORK, group_param="slug")),
]
LOST = "claim-lost"
"""Key of the fragment that says the claim is gone; no page ever polls with it."""

SHOWN_ON_THE_LIST = frozenset(
    {
        ErrorCode.QUEUE_EMPTY,
        ErrorCode.ALREADY_CLAIMED,
        ErrorCode.FREE_PICK_NOT_ALLOWED,
        ErrorCode.NOT_CLAIMED,
        ErrorCode.BOOKING_NOT_ACTIVE,
        ErrorCode.NOT_ELIGIBLE,
        ErrorCode.LEASE_EXPIRED,
        ErrorCode.START_REFUSED,
        ErrorCode.REQUEST_IN_PROGRESS,
        ErrorCode.REQUEST_NOT_STARTED,
        ErrorCode.NOT_CHECKED_IN,
        ErrorCode.OUTSIDE_BOOKING_WINDOW,
        ErrorCode.REQUEST_NOT_FOUND,
    }
)
"""Refusals that are answered with the list and a message, not an error page."""

VIDEO_LABELS: dict[VideoState, tuple[str | None, str]] = {
    VideoState.READY: (None, ""),
    VideoState.PENDING: ("admin.queue.video_pending", ""),
    VideoState.FAILED: ("admin.queue.video_failed", "danger"),
}
"""Catalogue key and chip tone per video state; a ready link needs no chip."""

PARTICIPANT_LABELS: dict[ParticipantState, tuple[str, str]] = {
    ParticipantState.NOT_YET: ("admin.queue.participant.not_yet", ""),
    ParticipantState.IN_LOBBY: ("admin.queue.participant.in_lobby", "warning"),
    ParticipantState.IN_CALL: ("admin.queue.participant.in_call", "success"),
    ParticipantState.ENDED: ("admin.queue.participant.ended", ""),
}
"""Catalogue key and chip tone per participant state on the claimed booking's page."""


@dataclass(frozen=True)
class RowView:
    """One row of the queue, written out."""

    id: int
    time: str
    first_name: str
    reference: str
    state: str
    tone: str
    direct: bool
    overdue: bool
    mine: bool
    can_claim: bool
    can_release: bool
    video: str | None
    video_tone: str
    routing: str = ""
    fallback: bool = False
    eligible: bool = True
    """The viewer holds every skill the row requires (ADR 0121 point 5)."""
    missing: str = ""
    """The skills the viewer lacks for the row, named for the page; empty when it fits."""
    can_check_in: bool = False
    """The appointment waits for its check-in (ADR 0121 step 7, choice I2)."""


@dataclass(frozen=True)
class ListView:
    """The queue as the page and its fragment show it, with the key the poller compares."""

    rows: list[RowView]
    has_next: bool
    free_pick: bool
    announcement: str
    key: str
    immediate: list[ImmediateRowView] = field(default_factory=list)
    """Requests without an appointment, oldest first (ADR 0121 step 7)."""
    has_next_immediate: bool = False
    """*Pick up next* has a request without an appointment to take."""


@dataclass(frozen=True)
class ClaimedView:
    """The claimed booking's status fragment: participant state, video state, poll key."""

    booking: ClaimedBooking
    time: str
    participant: str
    participant_tone: str
    video: str | None
    key: str
    polling: bool
    work: WorkView | None = None


def _service(request: Request) -> QueueService:
    """Return the application's queue service."""
    service: QueueService = request.app.state.queue_service
    return service


def _url(slug: str) -> str:
    """Return the queue's address."""
    return f"/admin/groups/{slug}/queue"


def _poll(request: Request, url: str, key: str, polling: bool = True) -> dict[str, object]:
    """Return the fragment's poll attributes: its URL with the key, and the interval."""
    return {
        "poll": f"{url}?seen={key}" if polling else None,
        "poll_seconds": settings_for(request).queue_refresh_seconds,
    }


def _state(row: QueueRow, _: BoundGettext) -> tuple[str, str]:
    """Return the row's state in words and its colour tone (never the only signal)."""
    if row.mine:
        return _("admin.queue.state.mine"), "success"
    if row.claimed:
        return _("admin.queue.state.claimed"), ""
    if row.in_lobby:
        return _("admin.queue.state.in_lobby"), "warning"
    return _("admin.queue.state.upcoming"), ""


def _row(
    row: QueueRow, view: QueueView, locale: RequestLocale, tz: tzinfo, routing: GroupRouting
) -> RowView:
    """Write one queue row out for the template."""
    label, tone = _state(row, locale.gettext)
    video_key, video_tone = VIDEO_LABELS[row.video]
    return RowView(
        id=row.booking_id,
        time=format_clock(row.start, tz, locale.code),
        first_name=row.first_name,
        reference=row.reference,
        state=label,
        tone=tone,
        direct=row.direct,
        overdue=row.overdue,
        mine=row.mine,
        can_claim=view.free_pick and not row.claimed and row.eligible and not row.awaiting_check_in,
        can_release=row.claimed
        and ((view.can_release_any and not row.mine) or (row.mine and not row.eligible)),
        video=locale.gettext(video_key) if video_key and not row.without_video else None,
        video_tone=video_tone,
        routing=routing_summary(routing, row.routing, locale.gettext, locale.code),
        fallback=row.fallback,
        eligible=row.eligible,
        missing=", ".join(skill_name(skill, locale.code) for skill in row.missing),
        can_check_in=row.awaiting_check_in and not row.claimed,
    )


async def _list_view(
    request: Request, locale: RequestLocale, context: AdminContext, slug: str
) -> ListView:
    """Load the queue and write it out; the key changes whenever a row would look different."""
    _ = locale.gettext
    view = await _service(request).view(context.principal, slug)
    rows = [_row(r, view, locale, view.group.tz, view.group.routing) for r in view.rows]
    immediate = await immediate_rows(request, locale, context, slug, view.free_pick)
    waiting = sum(1 for r in view.rows if r.in_lobby and not r.claimed)
    has_next = view.next_booking_id is not None
    shown = [
        (
            r.id,
            r.state,
            r.overdue,
            r.video,
            r.mine,
            r.can_claim,
            r.can_release,
            r.eligible,
            r.missing,
            r.can_check_in,
        )
        for r in rows
    ]
    return ListView(
        rows,
        has_next,
        view.free_pick,
        _("admin.queue.announcement", count=len(rows), waiting=waiting),
        key(shown, has_next, immediate.rows, immediate.has_next),
        immediate.rows,
        immediate.has_next,
    )


def _list_context(request: Request, slug: str, view: ListView) -> dict[str, object]:
    """Return what the list fragment renders."""
    return {
        "rows": view.rows,
        "has_next": view.has_next,
        "free_pick": view.free_pick,
        "announcement": view.announcement,
        "immediate": view.immediate,
        "has_next_immediate": view.has_next_immediate,
        **_poll(request, f"{_url(slug)}/live", view.key),
    }


async def render_list(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    refusal: ErrorCode | None = None,
) -> HTMLResponse:
    """Render the queue, with a refusal message when a pickup did not work out."""
    view = await _list_view(request, locale, context, slug)
    return group_page(
        request,
        locale,
        context,
        "admin/group_queue.html",
        {
            **_list_context(request, slug, view),
            "refusal": locale.gettext(ERROR_KEYS[refusal]) if refusal else None,
            # ADR 0082 point 11: the group falls back to this queue while the contact center
            # is down; who may manage its bookings may end it by hand.
            "fallback_since": admin_moment(request, locale, since)
            if (since := context.group.group.fallback_active_since)
            and context.group.group.fallback_active
            else None,
            "can_end_fallback": can_function(context.principal)(
                Permission.BOOKINGS_MANAGE, context.group
            ),
        },
        409 if refusal else 200,
        scripts=True,
    )


async def or_list[T](
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    attempt: Awaitable[T],
) -> T | HTMLResponse:
    """Return what ``attempt`` gives, or the queue with the reason it was refused."""
    try:
        return await attempt
    except DomainError as error:
        raise_refusal(error)
        if error.code not in SHOWN_ON_THE_LIST:
            raise
        return await render_list(request, locale, context, slug, error.code)


async def _claimed(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    attempt: Awaitable[ClaimedBooking],
) -> Response:
    """Go to the claimed booking, or back to the list with the reason it did not work."""
    claimed = await or_list(request, locale, context, slug, attempt)
    if isinstance(claimed, HTMLResponse):
        return claimed
    return RedirectResponse(f"{_url(slug)}/{claimed.booking_id}", status_code=303)


async def _done(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    attempt: Awaitable[None],
    outcome: str,
) -> Response:
    """Go back to the list with ``outcome``, or with the reason it did not work."""
    done = await or_list(request, locale, context, slug, attempt)
    return done if isinstance(done, HTMLResponse) else redirect_done(_url(slug), outcome)


def _claimed_view(booking: ClaimedBooking, locale: RequestLocale, tz: tzinfo) -> ClaimedView:
    """Write the claimed booking out; polling stops once nothing can change any more."""
    if booking.work is not None:
        work = work_view(booking.work.item, booking.work.request.handling, locale, tz)
        return ClaimedView(
            booking=booking,
            time=format_clock(booking.start, tz, locale.code),
            participant="",
            participant_tone="",
            video=None,
            key=key(work.state, work.can_start, work.can_complete),
            polling=True,
            work=work,
        )
    participant_key, participant_tone = PARTICIPANT_LABELS[booking.participant]
    video_key, _tone = VIDEO_LABELS[booking.video]
    return ClaimedView(
        booking=booking,
        time=format_clock(booking.start, tz, locale.code),
        participant=locale.gettext(participant_key),
        participant_tone=participant_tone,
        video=locale.gettext(video_key) if video_key else None,
        key=key(booking.video, booking.participant, booking.join_url is not None),
        polling=booking.video is not VideoState.FAILED
        and booking.participant is not ParticipantState.ENDED,
    )


def _claimed_context(request: Request, slug: str, view: ClaimedView) -> dict[str, object]:
    """Return what the claimed booking's status fragment renders."""
    url = f"{_url(slug)}/{view.booking.booking_id}/live"
    return {"claimed": view, **_poll(request, url, view.key, view.polling)}


async def _claimed_or_list(
    request: Request, locale: RequestLocale, context: AdminContext, slug: str, booking_id: int
) -> ClaimedView | HTMLResponse:
    """Return the claimed booking, or the list with the reason it cannot be shown."""
    attempt = _service(request).claimed(context.principal, slug, booking_id, locale.code)
    booking = await or_list(request, locale, context, slug, attempt)
    if isinstance(booking, HTMLResponse):
        return booking
    return _claimed_view(booking, locale, context.group.group.tz)


@router.get("/groups/{slug}/queue", response_class=HTMLResponse)
async def queue(request: Request, locale: Locale, slug: str, context: WorkQueue) -> HTMLResponse:
    """Show the group's own queue."""
    return await render_list(request, locale, context, slug)


@router.get("/groups/{slug}/queue/live", response_class=FragmentResponse)
async def queue_live(
    request: Request, locale: Locale, slug: str, context: WorkQueue, seen: Seen = ""
) -> Response:
    """Return the list fragment for the page's poller; 204 while ``seen`` is current."""
    view = await _list_view(request, locale, context, slug)
    return fragment(
        request,
        locale,
        context,
        "admin/_queue_list.html",
        seen,
        view.key,
        _list_context(request, slug, view),
    )


EndFallback = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.BOOKINGS_MANAGE, group_param="slug", fresh=True)),
]
"""Ending a group's fallback by hand (ADR 0082 point 11): who manages its bookings."""


@router.post("/groups/{slug}/queue/fallback/end", response_class=HTMLResponse)
async def end_fallback(
    request: Request, slug: str, _: SameOrigin, context: EndFallback
) -> Response:
    """End the group's fallback: bookings not yet claimed go to the contact center after all."""
    service: FallbackService = request.app.state.fallback_service
    try:
        ended = await service.end(context.principal, slug)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(_url(slug), "fallback_ended" if ended else "unchanged")


@router.post("/groups/{slug}/queue/next", response_class=HTMLResponse)
async def pick_up_next(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: WorkQueue
) -> Response:
    """Claim the booking that is next and open it."""
    attempt = _service(request).claim_next(context.principal, slug)
    return await _claimed(request, locale, context, slug, attempt)


@router.post("/groups/{slug}/queue/{booking_id}/claim", response_class=HTMLResponse)
async def claim(
    request: Request, locale: Locale, slug: str, booking_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Claim one chosen booking (only where the group allows free picking)."""
    attempt = _service(request).claim(context.principal, slug, booking_id)
    return await _claimed(request, locale, context, slug, attempt)


@router.get("/groups/{slug}/queue/{booking_id}", response_class=HTMLResponse)
async def claimed(
    request: Request, locale: Locale, slug: str, booking_id: int, context: WorkQueue
) -> Response:
    """Show a booking the agent claimed, with the link to join its meeting."""
    view = await _claimed_or_list(request, locale, context, slug, booking_id)
    if not isinstance(view, ClaimedView):
        return view
    return group_page(
        request,
        locale,
        context,
        "admin/queue_claimed.html",
        _claimed_context(request, slug, view),
        scripts=True,
    )


@router.get("/groups/{slug}/queue/{booking_id}/live", response_class=FragmentResponse)
async def claimed_live(
    request: Request,
    locale: Locale,
    slug: str,
    booking_id: int,
    context: WorkQueue,
    seen: Seen = "",
) -> Response:
    """Return the claimed booking's status fragment; 204 while ``seen`` is current.

    When the claim is gone meanwhile (a group admin released it, the booking was cancelled)
    the fragment says so and stops polling, instead of the list page the full request gets.
    """
    try:
        booking = await _service(request).claimed(context.principal, slug, booking_id, locale.code)
    except DomainError as error:
        raise_refusal(error)
        if error.code not in SHOWN_ON_THE_LIST:
            raise
        lost: dict[str, object] = {"message": locale.gettext(ERROR_KEYS[error.code]), "poll": None}
        return fragment(request, locale, context, "admin/_queue_claim_lost.html", seen, LOST, lost)
    view = _claimed_view(booking, locale, context.group.group.tz)
    return fragment(
        request,
        locale,
        context,
        "admin/_queue_claimed_status.html",
        seen,
        view.key,
        _claimed_context(request, slug, view),
    )


@router.post("/groups/{slug}/queue/{booking_id}/release", response_class=HTMLResponse)
async def release(
    request: Request, locale: Locale, slug: str, booking_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Put a claimed booking back in the queue."""
    attempt = _service(request).release(context.principal, slug, booking_id)
    return await _done(request, locale, context, slug, attempt, "released")


@router.post("/groups/{slug}/queue/{booking_id}/start", response_class=HTMLResponse)
async def start(
    request: Request, locale: Locale, slug: str, booking_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Start the treatment of claimed request work (ADR 0121 step 5)."""
    attempt = _service(request).start(context.principal, slug, booking_id)
    return await _claimed(request, locale, context, slug, attempt)


@router.post("/groups/{slug}/queue/{booking_id}/complete", response_class=HTMLResponse)
async def complete(
    request: Request, locale: Locale, slug: str, booking_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Complete request work: its unit of the pool is released (ADR 0121 step 5)."""
    attempt = _service(request).complete(context.principal, slug, booking_id)
    return await _done(request, locale, context, slug, attempt, "completed")


@router.post("/groups/{slug}/queue/{booking_id}/check-in", response_class=HTMLResponse)
async def check_in(
    request: Request, locale: Locale, slug: str, booking_id: int, _: SameOrigin, context: WorkQueue
) -> Response:
    """Check an appointment in: the participant has arrived (ADR 0121 step 7, choice I1)."""
    attempt = _service(request).check_in(context.principal, slug, booking_id)
    return await _done(request, locale, context, slug, attempt, "checked_in")
