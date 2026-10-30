"""Admin views of calls (ADR 0010): the agenda rows of bookings and a booking's timeline.

Both need ``bookings.view`` on the group. An agenda row (``agenda_routes`` shows them per
slot) has the first name only (no email or phone), the meeting and work item state in words,
whether the agent joined, the number of participants and a "not picked up" flag. The timeline
lists every recorded event with its reason code; nothing about participants beyond counts.

    GET  /admin/groups/{slug}/bookings/{booking_id}         bookings.view
    POST /admin/groups/{slug}/bookings/{booking_id}/retry   bookings.manage (fresh)

ADR 0068: the page also lists the booking's jobs and every request the platform made to Webex
and WxCC for it (method, path, status, tracking id, version; never a body), shows the version
that wrote each timeline event, and lets whoever holds ``bookings.manage`` plan a failed
meeting or work item job again.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AccessDenied, AdminContext, RequirePermission, can_function
from app.admin.forms import raise_refusal, read_form, redirect_done
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin, admin_moment
from app.admin.simulator_routes import simulator_view
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission
from app.domain.call_retry import CallRetryService
from app.domain.calls import BookingOverview, CallStatus, EventKind, not_picked_up
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import Purpose, labelled_answers
from app.domain.integration import IntegrationService
from app.domain.job_plan import RETRYABLE_JOBS, JobStatus, JobSummary, JobType
from app.domain.request_log import request_fields
from app.domain.scheduling import Group
from app.domain.services import BookingService
from app.i18n import BoundGettext
from app.i18n.dates import format_clock, format_day
from app.web.call_labels import (
    CALL_STATE_LABELS,
    EVENT_LABELS,
    JOB_LABELS,
    JOB_STATUS_LABELS,
    KIND_LABELS,
    TASK_STATE_LABELS,
    failure_hint,
)
from app.web.protection import RejectedRequest

router = APIRouter(prefix="/admin", include_in_schema=False)

ViewBookings = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.BOOKINGS_VIEW, group_param="slug")),
]
ManageBookings = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.BOOKINGS_MANAGE, group_param="slug", fresh=True)),
]


@dataclass(frozen=True)
class Chip:
    """A status written out, with a colour tone that is never the only signal."""

    label: str
    tone: str


@dataclass(frozen=True)
class AgendaRow:
    """One booking in the agenda."""

    id: int
    time: str
    kind: str
    first_name: str
    call: Chip
    task: Chip
    host_joined: bool
    participants: int
    not_picked_up: bool
    cancelled: bool
    waiting: bool = False
    meeting_id: str = ""
    """The Webex meeting's id, for support requests; not personal data (the join link is)."""


def integration_service(request: Request) -> IntegrationService:
    """Return the integration service."""
    service: IntegrationService = request.app.state.integration_service
    return service


def _bookings(request: Request) -> BookingService:
    """Return the application's booking service (for the group's form definition)."""
    service: BookingService = request.app.state.booking_service
    return service


def _chips(status: CallStatus, _: BoundGettext) -> tuple[Chip, Chip]:
    """Return the meeting and task state as chips."""
    call_key, call_tone = CALL_STATE_LABELS[status.call]
    task_key, task_tone = TASK_STATE_LABELS[status.task]
    return Chip(_(call_key), call_tone), Chip(_(task_key), task_tone)


def agenda_row(
    item: BookingOverview, group: Group, now: datetime, _: BoundGettext, code: str
) -> AgendaRow:
    """Turn a booking and its status into an agenda row."""
    booking, status, tz = item.booking, item.status, group.tz
    call, task = _chips(status, _)
    limit = timedelta(minutes=group.not_picked_up_minutes)
    return AgendaRow(
        id=booking.id or 0,
        time=f"{format_clock(booking.start, tz, code)}–{format_clock(booking.end, tz, code)}",
        kind=_(KIND_LABELS[booking.kind]),
        first_name=booking.contact.first_name if booking.contact else "–",
        call=call,
        task=task,
        host_joined=status.host_joined,
        participants=status.participants,
        not_picked_up=booking.is_active and not_picked_up(status, booking.start, now, limit),
        cancelled=not booking.is_active and not booking.awaits_rebook,
        waiting=booking.awaits_rebook,
        meeting_id=booking.meeting.id if booking.meeting else "",
    )


@dataclass(frozen=True)
class EventView:
    """One timeline entry; ``hint`` says what to check after a failure.

    ``message`` is the provider's own reason (ADR 0065), already translated into a sentence.
    """

    time: str
    label: str
    detail: str
    hint: str
    message: str = ""
    version: str = ""


@dataclass(frozen=True)
class JobView:
    """One of the booking's jobs (ADR 0068); ``retry`` is the job type a button plans again."""

    label: str
    when: str
    status: str
    tone: str
    attempts: int
    error: str
    retry: str = ""


@dataclass(frozen=True)
class RequestView:
    """One request to Webex or WxCC made for the booking (ADR 0068)."""

    when: str
    method: str
    path: str
    status: str
    tone: str
    error: str
    tracking: str
    ms: str
    version: str


def _job_views(
    request: Request,
    locale: Locale,
    jobs: list[JobSummary],
    can_retry: bool,
) -> list[JobView]:
    """Turn the booking's jobs into rows; a failed retryable job carries its retry button.

    Only the newest job of a type gets the button, and only while no job of that type is
    pending or running, so the page never offers to plan what is planned already.
    """
    _ = locale.gettext
    open_types = {j.type for j in jobs if j.status in (JobStatus.PENDING, JobStatus.RUNNING)}
    last_failed: dict[JobType, int] = {
        j.type: i for i, j in enumerate(jobs) if j.status is JobStatus.FAILED
    }
    rows = []
    for index, job in enumerate(jobs):
        key, tone = JOB_STATUS_LABELS[job.status]
        retry = (
            job.type.value
            if can_retry
            and job.type in RETRYABLE_JOBS
            and last_failed.get(job.type) == index
            and job.type not in open_types
            else ""
        )
        rows.append(
            JobView(
                _(JOB_LABELS[job.type]),
                admin_moment(request, locale, job.run_at),
                _(key),
                tone,
                job.attempts,
                job.last_error,
                retry,
            )
        )
    return rows


def _request_views(request: Request, locale: Locale, events: list[AuditEvent]) -> list[RequestView]:
    """Turn the booking's request events into rows, oldest first."""
    rows = []
    for event in events:
        fields = request_fields(event.detail)
        status = fields.get("status", "")
        rows.append(
            RequestView(
                admin_moment(request, locale, event.at),
                fields.get("method", ""),
                fields.get("path", ""),
                "–" if status in ("", "0") else status,
                "success" if event.outcome is Outcome.SUCCESS else "danger",
                event.reason,
                fields.get("tracking", ""),
                fields.get("ms", ""),
                fields.get("version", "") or "–",
            )
        )
    return rows


def retry_service(request: Request) -> CallRetryService:
    """Return the application's call retry service."""
    service: CallRetryService = request.app.state.call_retry_service
    return service


@router.get("/groups/{slug}/bookings/{booking_id}", response_class=HTMLResponse)
async def booking_detail(
    request: Request, locale: Locale, slug: str, booking_id: int, context: ViewBookings
) -> HTMLResponse:
    """Show one booking's call status and timeline."""
    _ = locale.gettext
    group = context.group.group
    try:
        timeline = await integration_service(request).timeline(context.principal, booking_id)
    except DomainError as error:
        if error.code is ErrorCode.NOT_PERMITTED:
            raise AccessDenied from error
        raise
    if timeline.booking.group_id != group.id:  # an id from another group, via this group's URL
        raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
    booking, status = timeline.booking, timeline.status
    call, task = _chips(status, _)
    tz, code = group.tz, locale.code
    events = [
        EventView(
            f"{format_day(e.at.astimezone(tz).date(), code)}, {format_clock(e.at, tz, code)}",
            _(EVENT_LABELS[e.kind]),
            e.detail,
            _(hint) if (hint := failure_hint(e.kind, e.detail, explained=bool(e.message))) else "",
            _("admin.booking.provider_message", message=e.message) if e.message else "",
            e.version,
        )
        for e in timeline.events
    ]
    can_retry = booking.is_active and can_function(context.principal)(
        Permission.BOOKINGS_MANAGE, context.group
    )
    return group_page(
        request,
        locale,
        context,
        "admin/booking_detail.html",
        {
            "reference": booking.reference,
            "when": f"{format_day(booking.start.astimezone(tz).date(), code)}, "
            f"{format_clock(booking.start, tz, code)}–{format_clock(booking.end, tz, code)}",
            "kind": _(KIND_LABELS[booking.kind]),
            "call": call,
            "task": task,
            "status": status,
            "meeting_id": booking.meeting.id if booking.meeting else "",
            "events": events,
            "jobs": _job_views(request, locale, timeline.jobs, can_retry),
            "requests": _request_views(request, locale, timeline.requests),
            "retry_action": f"/admin/groups/{slug}/bookings/{booking_id}/retry",
            "answers": labelled_answers(
                booking.contact.answers if booking.contact else None,
                await _bookings(request).get_form(group),
                Purpose.PLANNER_VIEW,
                code,
            ),
            "end_not_possible": any(e.kind is EventKind.END_NOT_POSSIBLE for e in timeline.events),
            "back": f"/admin/groups/{slug}/agenda?day={booking.start.astimezone(tz).date()}",
            "simulator": simulator_view(
                request,
                slug,
                booking,
                can_function(context.principal)(Permission.GROUP_EDIT, context.group),
                _,
            ),
        },
    )


@router.post("/groups/{slug}/bookings/{booking_id}/retry")
async def retry_job(
    request: Request,
    locale: Locale,
    slug: str,
    booking_id: int,
    _: SameOrigin,
    context: ManageBookings,
) -> Response:
    """Plan a failed meeting or work item job of the booking again (ADR 0068)."""
    form = await read_form(request, locale)
    try:
        job = JobType(form.text("job"))
    except ValueError as exc:
        raise RejectedRequest(400) from exc
    back = f"/admin/groups/{slug}/bookings/{booking_id}"
    try:
        await retry_service(request).retry(context.principal, slug, booking_id, job)
    except DomainError as error:
        if error.code in (ErrorCode.NOTHING_TO_RETRY, ErrorCode.BOOKING_NOT_ACTIVE):
            return redirect_done(back, "retry_not_possible", anchor="jobs")
        raise_refusal(error)
        raise
    return redirect_done(back, "retried", anchor="jobs")
