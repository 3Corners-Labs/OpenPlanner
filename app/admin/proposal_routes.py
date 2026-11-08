"""The planner's side of the rebooking assistant (ADR 0057 points 1, 5 and 10).

All under the group's *Agenda* tab and the right ``bookings.manage`` with a fresh login, like
the other planner actions (``planner_routes``):

    GET/POST .../agenda/bookings/{id}/propose        one booking: the assistant's time or
                                                     another free one ─► 303 agenda, "proposed"
    GET/POST .../agenda/slot-propose?start=          review a short slot's excess bookings
             (or ?day=&days=1..7: a day or a week)
                                                     keep, change or untick each proposal, or
                                                     ask to rebook; sent in one transaction
                                                     ─► 303 agenda; a changed slot shows again
    POST     .../agenda/bookings/{id}/proposal/withdraw   take a proposal back ─► waiting list
    POST     .../agenda/bookings/{id}/proposal/confirm    take it for the participant after a
                                                          phone call ─► agenda
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext
from app.admin.forms import (
    Field,
    FormData,
    Option,
    raise_refusal,
    read_form,
    redirect_done,
    slot_options,
)
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin
from app.admin.planner_routes import (
    ManageBookings,
    agenda_url,
    booking_view,
    confirmed_ids,
    planner_of,
    reason_field,
)
from app.domain.booking import ChangeReason, stored_id
from app.domain.errors import DomainError, ErrorCode
from app.domain.planner_service import ProposalRow, ReviewScope
from app.domain.scheduling import Group
from app.i18n.dates import format_clock, format_day, local_day
from app.web.errors import FormError, status_for
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest
from app.web.slot_values import parse_start

router = APIRouter(prefix="/admin", include_in_schema=False)

ASK = "rebook"
"""The choice that asks the participant to choose a time themselves (ADR 0044)."""

_REASONS = frozenset(r.value for r in ChangeReason)


@dataclass(frozen=True)
class ReviewRow:
    """One booking on the review page: who and when, its tick box and its new-time select."""

    id: int
    reference: str
    first_name: str
    when: str
    field: Field
    included: bool


def _time_field(
    row: ProposalRow, group: Group, form: FormData, locale: RequestLocale, name: str
) -> Field:
    """Return the select of new times for ``row``: the assistant's first, then the others."""
    _ = locale.gettext
    starts = ([row.proposed] if row.proposed else []) + [
        s for s in row.options if s != row.proposed
    ]
    return Field(
        name,
        _("admin.proposal.time"),
        "select",
        form.text(name) or (row.proposed.isoformat() if row.proposed else ASK),
        error=form.error_for(name),
        options=(
            *slot_options(starts, group.tz, locale.code),
            Option(ASK, _("admin.proposal.ask")),
        ),
    )


def _reason(form: FormData) -> ChangeReason | None:
    """Return the chosen fixed reason, or ``None`` (the form shows its error)."""
    choice = form.choice("reason", _REASONS)
    return ChangeReason(choice) if choice is not None else None


def _choice(form: FormData, name: str) -> datetime | None:
    """Return the chosen start, or ``None`` to ask to rebook; a forged value is a 400."""
    raw = form.text(name)
    if raw == ASK:
        return None
    chosen = parse_start(raw)
    if chosen is None:
        raise RejectedRequest(400)
    return chosen


# --- one booking ----------------------------------------------------------------------------


async def _single_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    booking_id: int,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the form that proposes a new time for one booking."""
    group = context.group.group
    try:
        row = await planner_of(request).booking_proposal(context.principal, group.slug, booking_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    return group_page(
        request,
        locale,
        context,
        "admin/planner_propose.html",
        {
            "booking": booking_view(row.booking, group, locale.code),
            "field": _time_field(row, group, form, locale, "start"),
            "reason": reason_field(form, locale.gettext),
            "found": row.proposed is not None,
            "back": agenda_url(group.slug, local_day(row.booking.start, group.tz)),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/agenda/bookings/{booking_id}/propose", response_class=HTMLResponse)
async def propose_form(
    request: Request, locale: Locale, slug: str, booking_id: int, context: ManageBookings
) -> HTMLResponse:
    """Show the assistant's new time for one booking, and the other free times."""
    return await _single_response(
        request, locale, context, booking_id, FormData({}, locale.gettext)
    )


@router.post("/groups/{slug}/agenda/bookings/{booking_id}/propose", response_class=HTMLResponse)
async def propose(
    request: Request,
    locale: Locale,
    slug: str,
    booking_id: int,
    _: SameOrigin,
    context: ManageBookings,
) -> Response:
    """Hold the chosen time for the booking; the participant gets the proposal."""
    form = await read_form(request, locale)
    chosen = _choice(form, "start")
    reason = _reason(form)
    if reason is None:
        return await _single_response(request, locale, context, booking_id, form, 422)
    group = context.group.group
    try:
        if chosen is not None:
            held = await planner_of(request).propose(
                context.principal, slug, booking_id, chosen, reason
            )
            day = local_day(held.start, group.tz)
            done = "proposed"
        else:
            waiting = await planner_of(request).request_rebook(
                context.principal, slug, booking_id, reason
            )
            day = local_day(waiting.start, group.tz)
            done = "rebook_requested"
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _single_response(request, locale, context, booking_id, form, status_for(error))
    return redirect_done(agenda_url(slug, day), done, 1)


# --- review a short slot, day or week --------------------------------------------------------


def _scope(start: str, day: str, days: str) -> ReviewScope:
    """Return the review's scope from the query or the form; anything else is a 400."""
    moment = parse_start(start)
    if moment is not None:
        return ReviewScope.slot(moment)
    try:
        first = date.fromisoformat(day)
        return ReviewScope.period(first, int(days or "1"))
    except (ValueError, DomainError):
        raise RejectedRequest(400) from None


def _scope_fields(scope: ReviewScope) -> dict[str, str]:
    """Return the hidden fields that carry ``scope`` from the page to its POST."""
    if scope.start is not None:
        return {"start": scope.start.isoformat()}
    return {"day": scope.day.isoformat() if scope.day else "", "days": str(scope.days)}


def _scope_label(scope: ReviewScope, group: Group, code: str) -> str:
    """Return what the review covers, as the page names it."""
    tz = group.tz
    if scope.start is not None:
        day = format_day(local_day(scope.start, tz), code)
        return f"{day}, {format_clock(scope.start, tz, code)}"
    first = scope.first_day(group)
    if scope.days == 1:
        return format_day(first, code)
    return f"{format_day(first, code)} – {format_day(first + timedelta(days=scope.days - 1), code)}"


async def _review_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    scope: ReviewScope,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the review page of the proposals in ``scope``, fresh from the agenda."""
    group = context.group.group
    tz, code = group.tz, locale.code
    try:
        rows = await planner_of(request).review_proposals(context.principal, group.slug, scope)
    except DomainError as error:
        raise_refusal(error)
        raise
    shown = [
        ReviewRow(
            stored_id(row.booking),
            row.booking.reference,
            row.booking.contact.first_name if row.booking.contact else "–",
            f"{format_day(local_day(row.booking.start, tz), code)}, "
            f"{format_clock(row.booking.start, tz, code)}",
            _time_field(row, group, form, locale, f"start_{stored_id(row.booking)}"),
            not form.values or form.checked(f"include_{stored_id(row.booking)}"),
        )
        for row in rows
    ]
    return group_page(
        request,
        locale,
        context,
        "admin/planner_slot_propose.html",
        {
            "scope": _scope_fields(scope),
            "when": _scope_label(scope, group, code),
            "rows": shown,
            "ids": ",".join(str(r.id) for r in shown),
            "reason": reason_field(form, locale.gettext),
            "back": agenda_url(group.slug, scope.first_day(group)),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/agenda/slot-propose", response_class=HTMLResponse)
async def slot_propose_form(
    request: Request,
    locale: Locale,
    slug: str,
    context: ManageBookings,
    start: str = "",
    day: str = "",
    days: str = "",
) -> HTMLResponse:
    """Show the assistant's proposals for one short slot (``start``) or for days (``day``)."""
    scope = _scope(start, day, days)
    return await _review_response(request, locale, context, scope, FormData({}, locale.gettext))


@router.post("/groups/{slug}/agenda/slot-propose", response_class=HTMLResponse)
async def slot_propose(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageBookings
) -> Response:
    """Send the reviewed proposals in one transaction (ADR 0057 point 5)."""
    form = await read_form(request, locale)
    scope = _scope(form.text("start"), form.text("day"), form.text("days"))
    ids = confirmed_ids(form.text("ids"))
    if ids is None:
        raise RejectedRequest(400)
    choices = {
        booking_id: _choice(form, f"start_{booking_id}")
        for booking_id in sorted(ids)
        if form.checked(f"include_{booking_id}")
    }
    reason = _reason(form)
    if not choices:
        form.errors.append(FormError(None, locale.gettext("admin.proposal.none_chosen")))
    if reason is None or not choices:
        return await _review_response(request, locale, context, scope, form, 422)
    try:
        sent = await planner_of(request).send_review(
            context.principal, slug, scope, reason, choices
        )
    except DomainError as error:
        raise_refusal(error)
        if error.code is not ErrorCode.SLOT_CHANGED:
            raise
        form.refusal(error)
        fresh = FormData({"reason": reason.value}, locale.gettext)
        fresh.errors.extend(form.errors)
        return await _review_response(request, locale, context, scope, fresh, 409)
    group = context.group.group
    return redirect_done(agenda_url(slug, scope.first_day(group)), "proposals_sent", sent)


# --- withdraw, or confirm on the participant's behalf --------------------------------------


@router.post("/groups/{slug}/agenda/bookings/{booking_id}/proposal/withdraw")
async def withdraw(
    request: Request,
    locale: Locale,
    slug: str,
    booking_id: int,
    _: SameOrigin,
    context: ManageBookings,
) -> Response:
    """Take a proposal back; the booking keeps awaiting a new time."""
    waiting = f"/admin/groups/{slug}/agenda/waiting"
    try:
        await planner_of(request).withdraw(context.principal, slug, booking_id)
    except DomainError as error:
        raise_refusal(error)
        if error.code is not ErrorCode.PROPOSAL_NOT_OPEN:
            raise
        return redirect_done(waiting, "proposal_gone")
    return redirect_done(waiting, "proposal_withdrawn")


@router.post("/groups/{slug}/agenda/bookings/{booking_id}/proposal/confirm")
async def confirm(
    request: Request,
    locale: Locale,
    slug: str,
    booking_id: int,
    _: SameOrigin,
    context: ManageBookings,
) -> Response:
    """Take the proposed time for the participant; they get the change mail."""
    group = context.group.group
    try:
        moved = await planner_of(request).confirm_proposal(context.principal, slug, booking_id)
    except DomainError as error:
        raise_refusal(error)
        if error.code is ErrorCode.BOOKING_NOT_FOUND:
            raise
        return redirect_done(f"/admin/groups/{slug}/agenda/waiting", "proposal_gone")
    return redirect_done(
        agenda_url(slug, local_day(moved.start, group.tz)), "proposal_confirmed_by_staff"
    )
