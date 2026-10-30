"""A group's agenda (ADR 0010, 0044): per slot the agents on duty, the bookings and shortfalls.

The day view lists every slot of the day with its agent count from the effective staffing,
its number of confirmed bookings and, below it, the bookings (first name and call status, see
``call_routes.agenda_row``). The week view is a table of slot times (rows) by days (columns)
with the same counts; each cell links to its slot in the day view. A shortfall (more bookings
than agents) is written out with an icon, never shown by colour alone. The week view needs no
JavaScript. The day view's table refreshes itself (ADR 0010, 0072): ``static/live-region.js``
polls ``agenda/live`` every ``MEETING_STATUS_REFRESH_SECONDS`` and swaps the table in when
something changed; without the script the page shows its status as loaded and offers a
"Refresh" link. Each load and each poll refreshes a status older than that window from the
video provider (``MeetingStatusService``), so a missed webhook is corrected within a window.

With ``bookings.manage`` (ADR 0044) the day view links each booking a planner may still
change to its move and cancel forms (``planner_routes``) and offers "leave as is" on a slot
with an open shortfall.

    GET /admin/groups/{slug}/agenda?day=YYYY-MM-DD          bookings.view
    GET /admin/groups/{slug}/agenda/live?day=…&seen=KEY     bookings.view  (fragment, 204 if same)
    GET /admin/groups/{slug}/agenda/week?week=YYYY-MM-DD    bookings.view
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, can_function
from app.admin.call_routes import AgendaRow, ViewBookings, agenda_row
from app.admin.group_routes import group_page
from app.admin.live import Seen, fragment, key
from app.admin.pages import Locale
from app.admin.shortfalls import agenda_service
from app.domain.agenda import AgendaSlot
from app.domain.authorization import Permission
from app.domain.booking import BookingKind
from app.domain.booking_changes import planner_may_change
from app.domain.calls import CallState
from app.domain.meeting_status_service import MeetingStatusService
from app.domain.ports import Clock
from app.domain.scheduling import Group
from app.i18n import BoundGettext
from app.i18n.dates import format_clock, format_day, format_day_short
from app.web.locale import RequestLocale
from app.web.organisation_settings import settings_for
from app.web.templates import FragmentResponse

router = APIRouter(prefix="/admin", include_in_schema=False)

WEEK_DAYS = 7


@dataclass(frozen=True)
class SlotView:
    """One slot as the day and week views show it.

    ``staffing`` is ``None`` for a slot that only holds cancelled or finished bookings
    outside today's staffing: there is nothing to count against.
    """

    anchor: str
    time: str
    staffing: str | None
    shortfall: str
    closed: bool
    rows: list[AgendaRow] = field(default_factory=list)
    start: str = ""
    accepted: bool = False
    can_accept: bool = False


def _anchor(start: datetime) -> str:
    """Return the HTML id of the slot starting at ``start`` (UTC, so unique across DST)."""
    return f"slot-{start:%Y%m%dT%H%M}"


def _slot_view(
    slot: AgendaSlot,
    group: Group,
    code: str,
    _: BoundGettext,
    *,
    manage: bool = False,
    now: datetime | None = None,
) -> SlotView:
    """Return the view of one agenda slot, without its bookings.

    ``manage`` (with ``now``) offers "leave as is" on an open shortfall that lies ahead.
    """
    tz = group.tz
    return SlotView(
        anchor=_anchor(slot.start),
        time=f"{format_clock(slot.start, tz, code)}–{format_clock(slot.end, tz, code)}",
        staffing=_("admin.agenda.slot_staffing", agents=slot.agents, booked=slot.booked),
        shortfall=_("admin.agenda.shortfall", count=slot.shortfall) if slot.shortfall else "",
        closed=slot.closed,
        start=slot.start.isoformat(),
        accepted=slot.accepted,
        can_accept=manage and now is not None and bool(slot.open_shortfall) and slot.start >= now,
    )


REACH = timedelta(days=3660)
"""How far from today ``?day=`` and ``?week=`` may go; further is treated as invalid.

An input-sanity bound, not a tunable: it keeps date arithmetic away from ``date.min`` and
``date.max`` (an ``OverflowError`` would be a 500).
"""


def _chosen(raw: str | None, today: date) -> date:
    """Return the date of ``?day=`` or ``?week=``, or ``today`` when absent or invalid."""
    try:
        chosen = date.fromisoformat(raw) if raw else today
    except ValueError:
        return today
    return chosen if abs(chosen - today) <= REACH else today


def _meeting_status(request: Request) -> MeetingStatusService:
    """Return the service that refreshes stale meeting statuses from the video provider."""
    service: MeetingStatusService = request.app.state.meeting_status_service
    return service


async def _day(
    request: Request, locale: RequestLocale, context: AdminContext, slug: str, day: str | None
) -> tuple[dict[str, object], str]:
    """Load one day of the agenda; return what its table renders and the key of that table.

    The key changes whenever a slot or row would look different, so the poller is only sent
    a new table when something changed. Past days do not poll: nothing on them changes.
    """
    _, code = locale.gettext, locale.code
    group = context.group.group
    clock: Clock = request.app.state.clock
    now = clock.now()
    today = now.astimezone(group.tz).date()
    chosen = _chosen(day, today)
    (agenda_day,) = await agenda_service(request).days(context.principal, slug, chosen, 1)
    overview = await _meeting_status(request).day_overview(context.principal, slug, chosen)
    manage = can_function(context.principal)(Permission.BOOKINGS_MANAGE, context.group)
    slots = {
        s.start: _slot_view(s, group, code, _, manage=manage, now=now) for s in agenda_day.slots
    }
    direct: list[AgendaRow] = []
    changeable: set[int] = set()
    running = 0
    for item in overview:
        row = agenda_row(item, group, now, _, code)
        booking = item.booking
        running += booking.is_active and item.status.call is CallState.IN_PROGRESS
        if manage and planner_may_change(booking, group, now):
            changeable.add(row.id)
        if booking.kind is BookingKind.DIRECT:
            direct.append(row)
            continue
        if booking.start not in slots:
            tz = group.tz
            slots[booking.start] = SlotView(
                _anchor(booking.start),
                f"{format_clock(booking.start, tz, code)}–{format_clock(booking.end, tz, code)}",
                None,
                "",
                closed=False,
            )
        slots[booking.start].rows.append(row)
    ordered = [slots[start] for start in sorted(slots)]
    shown = key(ordered, direct, sorted(changeable))
    unpicked = sum(r.not_picked_up for s in ordered for r in s.rows) + sum(
        r.not_picked_up for r in direct
    )
    return (
        {
            "day_label": format_day(chosen, code),
            "previous_day": (chosen - timedelta(days=1)).isoformat(),
            "next_day": (chosen + timedelta(days=1)).isoformat(),
            "today": today.isoformat(),
            "this_day": chosen.isoformat(),
            "week_href": f"/admin/groups/{slug}/agenda/week?week={chosen.isoformat()}",
            "slots": ordered,
            "direct": direct,
            "changeable": changeable,
            "manage": manage,
            "announcement": _("admin.agenda.live_status", running=running, unpicked=unpicked),
            "poll": f"/admin/groups/{slug}/agenda/live?day={chosen.isoformat()}&seen={shown}"
            if chosen >= today
            else None,
            "poll_seconds": settings_for(request).meeting_status_refresh_seconds,
        },
        shown,
    )


@router.get("/groups/{slug}/agenda", response_class=HTMLResponse)
async def agenda(
    request: Request, locale: Locale, slug: str, context: ViewBookings, day: str | None = None
) -> HTMLResponse:
    """Show the group's slots of one day with agents, bookings and their call status."""
    page, _shown = await _day(request, locale, context, slug, day)
    return group_page(request, locale, context, "admin/group_agenda.html", page, scripts=True)


@router.get("/groups/{slug}/agenda/live", response_class=FragmentResponse)
async def agenda_live(
    request: Request,
    locale: Locale,
    slug: str,
    context: ViewBookings,
    day: str | None = None,
    seen: Seen = "",
) -> Response:
    """Return the day's table for the page's poller; 204 while ``seen`` is current."""
    page, shown = await _day(request, locale, context, slug, day)
    return fragment(request, locale, context, "admin/_agenda_table.html", seen, shown, page)


@dataclass(frozen=True)
class WeekCell:
    """One slot in the week table, linking to the day view; ``None`` cells are empty."""

    href: str
    staffing: str
    shortfall: str
    closed: bool


@dataclass(frozen=True)
class WeekRow:
    """One slot time (row header) with a cell per day."""

    time: str
    cells: list[WeekCell | None]


@router.get("/groups/{slug}/agenda/week", response_class=HTMLResponse)
async def agenda_week(
    request: Request, locale: Locale, slug: str, context: ViewBookings, week: str | None = None
) -> HTMLResponse:
    """Show the group's slots of one week (Monday to Sunday) with agents and bookings."""
    _, code = locale.gettext, locale.code
    group = context.group.group
    tz = group.tz
    clock: Clock = request.app.state.clock
    today = clock.now().astimezone(tz).date()
    chosen = _chosen(week, today)
    monday = chosen - timedelta(days=chosen.weekday())
    days = await agenda_service(request).days(context.principal, slug, monday, WEEK_DAYS)
    by_time: dict[time, list[WeekCell | None]] = {}
    labels: dict[time, str] = {}
    for index, agenda_day in enumerate(days):
        for slot in agenda_day.slots:
            view = _slot_view(slot, group, code, _)
            local = slot.start.astimezone(tz).time()
            labels.setdefault(local, view.time)
            cells = by_time.setdefault(local, [None] * WEEK_DAYS)
            href = f"/admin/groups/{slug}/agenda?day={agenda_day.day.isoformat()}#{view.anchor}"
            cells[index] = WeekCell(href, view.staffing or "", view.shortfall, view.closed)
    shortfalls = sum(1 for d in days for s in d.slots if s.shortfall)
    return group_page(
        request,
        locale,
        context,
        "admin/group_agenda_week.html",
        {
            "week_label": _("admin.agenda.week_title", day=format_day(monday, code)),
            "day_headers": [
                (format_day_short(d.day, code), f"/admin/groups/{slug}/agenda?day={d.day}")
                for d in days
            ],
            "rows": [WeekRow(labels[local], by_time[local]) for local in sorted(by_time)],
            "week_shortfalls": shortfalls,
            "previous_week": (monday - timedelta(days=WEEK_DAYS)).isoformat(),
            "next_week": (monday + timedelta(days=WEEK_DAYS)).isoformat(),
            "this_week": today.isoformat(),
            "day_href": f"/admin/groups/{slug}/agenda?day={monday.isoformat()}",
        },
    )
