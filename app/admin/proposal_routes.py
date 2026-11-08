"""The planner's side of the rebooking assistant (ADR 0057 points 1, 5 and 10).

All under the group's *Agenda* tab and the right ``bookings.manage`` with a fresh login, like
the other planner actions (``planner_routes``):

    GET/POST .../agenda/bookings/{id}/propose        one booking: the assistant's time or
                                                     another free one ─► 303 agenda, "proposed"
    GET/POST .../agenda/slot-propose?start=          review a short slot's excess bookings:
                                                     keep, change or untick each proposal, or
                                                     ask to rebook; sent in one transaction
                                                     ─► 303 agenda; a changed slot shows again
    POST     .../agenda/bookings/{id}/proposal/withdraw   take a proposal back ─► waiting list
    POST     .../agenda/bookings/{id}/proposal/confirm    take it for the participant after a
                                                          phone call ─► agenda
"""

from dataclasses import dataclass
from datetime import datetime

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
from app.domain.booking import ChangeReason
from app.domain.errors import DomainError, ErrorCode
from app.domain.planner_service import ProposalRow
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


def _choice(form: FormData, name: str) -> datetime | None | str:
    """Return the chosen start, ``None`` to ask to rebook, or ``"bad"`` for a forged value."""
    raw = form.text(name)
    if raw == ASK:
        return None
    return parse_start(raw) or "bad"


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
    if chosen == "bad":
        raise RejectedRequest(400)
    if reason is None:
        return await _single_response(request, locale, context, booking_id, form, 422)
    group = context.group.group
    try:
        if isinstance(chosen, datetime):
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


# --- review a short slot ---------------------------------------------------------------------


async def _review_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    start: datetime,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the review page of a short slot's proposals, fresh from the agenda."""
    group = context.group.group
    tz, code = group.tz, locale.code
    try:
        rows = await planner_of(request).slot_proposals(context.principal, group.slug, start)
    except DomainError as error:
        raise_refusal(error)
        raise
    shown = [
        ReviewRow(
            row.booking.id or 0,
            row.booking.reference,
            row.booking.contact.first_name if row.booking.contact else "–",
            f"{format_day(local_day(row.booking.start, tz), code)}, "
            f"{format_clock(row.booking.start, tz, code)}",
            _time_field(row, group, form, locale, f"start_{row.booking.id}"),
            not form.values or form.checked(f"include_{row.booking.id}"),
        )
        for row in rows
    ]
    return group_page(
        request,
        locale,
        context,
        "admin/planner_slot_propose.html",
        {
            "start": start.isoformat(),
            "when": f"{format_day(local_day(start, tz), code)}, {format_clock(start, tz, code)}",
            "rows": shown,
            "ids": ",".join(str(r.id) for r in shown),
            "reason": reason_field(form, locale.gettext),
            "back": agenda_url(group.slug, local_day(start, tz)),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/agenda/slot-propose", response_class=HTMLResponse)
async def slot_propose_form(
    request: Request, locale: Locale, slug: str, context: ManageBookings, start: str = ""
) -> HTMLResponse:
    """Show the assistant's proposals for the last-made bookings of a short slot."""
    moment = parse_start(start)
    if moment is None:
        raise RejectedRequest(400)
    return await _review_response(request, locale, context, moment, FormData({}, locale.gettext))


@router.post("/groups/{slug}/agenda/slot-propose", response_class=HTMLResponse)
async def slot_propose(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageBookings
) -> Response:
    """Send the reviewed proposals of the slot in one transaction (ADR 0057 point 5)."""
    form = await read_form(request, locale)
    moment = parse_start(form.text("start"))
    ids = confirmed_ids(form.text("ids"))
    if moment is None or ids is None:
        raise RejectedRequest(400)
    choices: dict[int, datetime | None] = {}
    for booking_id in sorted(ids):
        if not form.checked(f"include_{booking_id}"):
            continue
        chosen = _choice(form, f"start_{booking_id}")
        if chosen == "bad":
            raise RejectedRequest(400)
        choices[booking_id] = chosen if isinstance(chosen, datetime) else None
    reason = _reason(form)
    if not choices:
        form.errors.append(FormError(None, locale.gettext("admin.proposal.none_chosen")))
    if reason is None or not choices:
        return await _review_response(request, locale, context, moment, form, 422)
    try:
        sent = await planner_of(request).propose_slot(
            context.principal, slug, moment, reason, choices
        )
    except DomainError as error:
        raise_refusal(error)
        if error.code is not ErrorCode.SLOT_CHANGED:
            raise
        form.refusal(error)
        fresh = FormData({"reason": reason.value}, locale.gettext)
        fresh.errors.extend(form.errors)
        return await _review_response(request, locale, context, moment, fresh, 409)
    group = context.group.group
    return redirect_done(agenda_url(slug, local_day(moment, group.tz)), "proposals_sent", sent)


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
