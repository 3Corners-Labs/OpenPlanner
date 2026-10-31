"""Admin routes for holiday calendars (ADR 0025).

Every route depends on exactly one ``RequirePermission``; forms and changes are ``fresh``.
Changes are POST forms with the Fetch Metadata check, answered with a redirect and a
confirmation. ``HolidayService`` authorises again, validates and audits.

    GET  /admin/holidays                                holidays.assign  calendars, organisations
    POST /admin/holidays                                holidays.manage  create a calendar
    GET  /admin/holidays/{calendar}?year=               holidays.manage  dates of one year
    POST /admin/holidays/{calendar}/days                holidays.manage  add one date
    POST /admin/holidays/{calendar}/national            holidays.manage  add the Dutch holidays
    POST /admin/holidays/{calendar}/days/{id}/delete    holidays.manage  remove a date
    POST /admin/holidays/{calendar}/delete              holidays.manage  delete the calendar
    GET  /admin/organisations/{org}/holidays            holidays.assign  the organisation's choice
    POST /admin/organisations/{org}/holidays            holidays.assign  save it
    GET  /admin/groups/{slug}/holidays                  schedule.manage  the group's choice
    POST /admin/groups/{slug}/holidays                  schedule.manage  inherit or own calendars
    POST /admin/groups/{slug}/holidays/{day}/open       schedule.manage  open as usual that day
    POST /admin/groups/{slug}/holidays/{day}/close      schedule.manage  closed again that day
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData, done_message, raise_refusal, read_form, redirect_done
from app.admin.group_routes import ManageGroup, group_page
from app.admin.pages import Locale, SameOrigin, organisation_header, page
from app.admin.shortfalls import group_shortfalls
from app.domain.authorization import Permission
from app.domain.errors import DomainError, ErrorCode
from app.domain.holiday_service import DayState, HolidayService
from app.domain.holidays import (
    MAX_YEAR,
    MIN_YEAR,
    HolidayCalendar,
    HolidaySource,
    dutch_national_holidays,
)
from app.domain.ports import Clock
from app.i18n.dates import format_day
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[AdminContext, Depends(RequirePermission(Permission.HOLIDAYS_MANAGE, fresh=True))]
"""The permission of every calendar page and change (superuser)."""

Assign = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.HOLIDAYS_ASSIGN, organisation_param="org", fresh=True)),
]
"""The permission of an organisation's calendar choice."""

STATE_LABELS: dict[DayState, str] = {
    DayState.CLOSED: "admin.holidays.state_closed",
    DayState.OPEN: "admin.holidays.state_open",
    DayState.EXCEPTION: "admin.holidays.state_exception",
}


def holiday_service(request: Request) -> HolidayService:
    """Return the application's holiday service."""
    service: HolidayService = request.app.state.holiday_service
    return service


@dataclass(frozen=True)
class CalendarOption:
    """A calendar as a checkbox: id, name and whether it is ticked."""

    id: int
    name: str
    checked: bool


def _options(calendars: Sequence[HolidayCalendar], chosen: frozenset[int]) -> list[CalendarOption]:
    """Return the calendars as checkboxes, ticking ``chosen``."""
    return [CalendarOption(c.id, c.name, c.id in chosen) for c in calendars]


def _chosen(form: FormData) -> frozenset[int]:
    """Return the ticked calendars (inputs named ``calendar-<id>``); malformed names are ignored."""
    ids = (name.removeprefix("calendar-") for name in form.values if name.startswith("calendar-"))
    return frozenset(int(i) for i in ids if i.isdigit() and len(i) <= 9)


def _this_year(request: Request) -> int:
    """Return the current year (UTC is precise enough for choosing a year to show)."""
    clock: Clock = request.app.state.clock
    return clock.now().year


# --- overview -------------------------------------------------------------------------------


def _calendar_fields(form: FormData, locale: RequestLocale) -> list[Field]:
    """Return the inputs of the new-calendar form."""
    _ = locale.gettext
    return [
        Field("name", _("admin.field.name"), value=form.text("name"), error=form.error_for("name")),
        Field(
            "slug",
            _("admin.field.slug"),
            value=form.text("slug"),
            hint=_("admin.holidays.slug_hint"),
            error=form.error_for("slug"),
            attributes={"spellcheck": "false", "autocapitalize": "none"},
        ),
    ]


async def _overview_response(
    request: Request, locale: RequestLocale, context: AdminContext, form: FormData, status: int
) -> HTMLResponse:
    """Render the calendars, the new-calendar form and the organisations."""
    overview = await holiday_service(request).overview(context.principal)
    response = page(
        request,
        locale,
        "admin/holidays.html",
        context.principal,
        {
            "calendars": overview.calendars,
            "fields": _calendar_fields(form, locale),
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
        },
    )
    response.status_code = status
    return response


@router.get("/holidays", response_class=HTMLResponse)
async def overview(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.HOLIDAYS_MANAGE))],
) -> HTMLResponse:
    """List the shared calendars (*Platform → Holiday calendars*, ADR 0047 §4)."""
    return await _overview_response(request, locale, context, FormData({}, locale.gettext), 200)


@router.post("/holidays", response_class=HTMLResponse)
async def create_calendar(
    request: Request, locale: Locale, _: SameOrigin, context: Manage
) -> Response:
    """Create an empty calendar and open it."""
    form = await read_form(request, locale)
    try:
        calendar = await holiday_service(request).create_calendar(
            context.principal, form.text("slug"), form.text("name")
        )
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _overview_response(request, locale, context, form, 422)
    return redirect_done(f"/admin/holidays/{calendar.slug}", "created")


# --- one calendar ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HolidayView:
    """One date of a calendar for display."""

    id: int | None
    day: str
    name: str


def _holiday_fields(form: FormData, locale: RequestLocale) -> list[Field]:
    """Return the inputs of the add-date form."""
    _ = locale.gettext
    return [
        Field(
            "holiday_day",
            _("admin.field.day"),
            "date",
            form.text("holiday_day"),
            error=form.error_for("holiday_day"),
        ),
        Field(
            "name",
            _("admin.holidays.holiday_name"),
            value=form.text("name"),
            error=form.error_for("name"),
        ),
    ]


async def _calendar_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    year: int,
    form: FormData,
    status: int,
) -> HTMLResponse:
    """Render one calendar's dates of ``year`` with its forms."""
    detail = await holiday_service(request).calendar(context.principal, slug, year)
    response = page(
        request,
        locale,
        "admin/holiday_calendar.html",
        context.principal,
        {
            "calendar": detail.calendar,
            "year": detail.year,
            "years": [y for y in (year - 1, year + 1) if MIN_YEAR <= y <= MAX_YEAR],
            "holidays": [
                HolidayView(h.id, format_day(h.day, locale.code), h.name) for h in detail.holidays
            ],
            "users": detail.users,
            "fields": _holiday_fields(form, locale),
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
        },
    )
    response.status_code = status
    return response


def _year(raw: str | None, fallback: int) -> int:
    """Return the year from ``?year=`` when it is a plausible one, else ``fallback``."""
    if raw and raw.isdigit() and MIN_YEAR <= int(raw) <= MAX_YEAR:
        return int(raw)
    return fallback


@router.get("/holidays/{calendar}", response_class=HTMLResponse)
async def calendar_page(
    request: Request, locale: Locale, calendar: str, context: Manage, year: str | None = None
) -> HTMLResponse:
    """Show the dates of one calendar in one year (this year by default)."""
    shown = _year(year, _this_year(request))
    form = FormData({}, locale.gettext)
    return await _calendar_response(request, locale, context, calendar, shown, form, 200)


def _done(calendar: str, year: int, done: str) -> Response:
    """Redirect to the calendar page of ``year`` with a confirmation."""
    return redirect_done(f"/admin/holidays/{calendar}?year={year}", done)


@router.post("/holidays/{calendar}/days", response_class=HTMLResponse)
async def add_holiday(
    request: Request, locale: Locale, calendar: str, _: SameOrigin, context: Manage
) -> Response:
    """Add one date to the calendar."""
    form = await read_form(request, locale)
    day = form.day("holiday_day")
    year = day.year if day and MIN_YEAR <= day.year <= MAX_YEAR else _this_year(request)
    if day is not None:
        try:
            await holiday_service(request).add_holiday(
                context.principal, calendar, day, form.text("name")
            )
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return _done(calendar, year, "added")
    return await _calendar_response(request, locale, context, calendar, year, form, 422)


@router.post("/holidays/{calendar}/national", response_class=HTMLResponse)
async def add_national_holidays(
    request: Request, locale: Locale, calendar: str, _: SameOrigin, context: Manage
) -> Response:
    """Add the Dutch national holidays of the chosen year, named in the admin's language."""
    form = await read_form(request, locale)
    year = form.integer("year")
    if year is not None:
        try:
            entries = [(d, locale.gettext(key)) for d, key in dutch_national_holidays(year)]
            await holiday_service(request).add_holidays(context.principal, calendar, entries)
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return _done(calendar, year, "added")
    return await _calendar_response(
        request, locale, context, calendar, _this_year(request), form, 422
    )


@router.post("/holidays/{calendar}/days/{holiday_id}/delete", response_class=HTMLResponse)
async def remove_holiday(
    request: Request, calendar: str, holiday_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Remove one date from the calendar."""
    year = _year(request.query_params.get("year"), _this_year(request))
    try:
        await holiday_service(request).remove_holiday(context.principal, calendar, holiday_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    return _done(calendar, year, "removed")


@router.post("/holidays/{calendar}/delete", response_class=HTMLResponse)
async def delete_calendar(
    request: Request, locale: Locale, calendar: str, _: SameOrigin, context: Manage
) -> Response:
    """Delete a calendar that nobody uses."""
    try:
        await holiday_service(request).delete_calendar(context.principal, calendar)
    except DomainError as error:
        raise_refusal(error)
        if error.code is not ErrorCode.HOLIDAY_CALENDAR_IN_USE:
            raise
        form = FormData({}, locale.gettext)
        form.refusal(error)
        return await _calendar_response(
            request, locale, context, calendar, _this_year(request), form, 409
        )
    return redirect_done("/admin/holidays", "removed")


# --- organisations --------------------------------------------------------------------------


async def _organisation_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    form: FormData,
    status: int,
) -> HTMLResponse:
    """Render an organisation's calendar choice."""
    choice = await holiday_service(request).organisation_choice(context.principal, org)
    chosen = _chosen(form) if form.values else choice.chosen
    response = page(
        request,
        locale,
        "admin/holiday_organisation.html",
        context.principal,
        {
            "organisation": choice.organisation,
            "org_header": organisation_header(
                request,
                locale,
                context.principal,
                choice.organisation.name,
                choice.organisation.slug,
            ),
            "options": _options(choice.calendars, chosen),
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
        },
    )
    response.status_code = status
    return response


@router.get("/organisations/{org}/holidays", response_class=HTMLResponse)
async def organisation_page(
    request: Request, locale: Locale, org: str, context: Assign
) -> HTMLResponse:
    """Show which calendars the organisation's groups follow."""
    form = FormData({}, locale.gettext)
    return await _organisation_response(request, locale, context, org, form, 200)


@router.post("/organisations/{org}/holidays", response_class=HTMLResponse)
async def save_organisation(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Assign
) -> Response:
    """Save the organisation's calendars."""
    form = await read_form(request, locale)
    try:
        changed = await holiday_service(request).choose_for_organisation(
            context.principal, org, _chosen(form)
        )
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _organisation_response(request, locale, context, org, form, 422)
    return redirect_done(
        f"/admin/organisations/{org}/holidays", "saved" if changed else "unchanged"
    )


# --- groups ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class UpcomingView:
    """One upcoming holiday of a group for display."""

    iso: str
    day: str
    name: str
    state: str
    label: str


async def _group_response(
    request: Request, locale: RequestLocale, context: AdminContext, form: FormData, status: int
) -> HTMLResponse:
    """Render the group's holiday choice and its upcoming holidays."""
    _ = locale.gettext
    view = await holiday_service(request).group_holidays(
        context.principal, context.group.group.slug
    )
    source = form.text("source") or view.choice.source.value
    own = _chosen(form) if form.values else view.choice.calendar_ids
    names = {c.id: c.name for c in view.calendars}
    return group_page(
        request,
        locale,
        context,
        "admin/group_holidays.html",
        {
            "shortfalls": await group_shortfalls(request, locale, context),
            "source": source,
            "inherited": [names[i] for i in sorted(view.inherited) if i in names],
            "options": _options(view.calendars, own),
            "upcoming": [
                UpcomingView(
                    u.holiday.day.isoformat(),
                    format_day(u.holiday.day, locale.code),
                    u.holiday.name,
                    u.state.value,
                    _(STATE_LABELS[u.state]),
                )
                for u in view.upcoming
            ],
            "source_error": form.error_for("source"),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/holidays", response_class=HTMLResponse)
async def group_holidays(
    request: Request, locale: Locale, slug: str, context: ManageGroup
) -> HTMLResponse:
    """Show the group's holiday choice and its upcoming holidays."""
    return await _group_response(request, locale, context, FormData({}, locale.gettext), 200)


@router.post("/groups/{slug}/holidays", response_class=HTMLResponse)
async def save_group_holidays(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageGroup
) -> Response:
    """Let the group inherit the organisation's calendars or choose its own."""
    form = await read_form(request, locale)
    source = form.choice("source", {s.value for s in HolidaySource})
    if source is not None:
        try:
            changed = await holiday_service(request).set_group_calendars(
                context.principal, slug, HolidaySource(source), _chosen(form)
            )
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return redirect_done(
                f"/admin/groups/{slug}/holidays", "saved" if changed else "unchanged"
            )
    return await _group_response(request, locale, context, form, 422)


async def _set_open(
    request: Request, slug: str, day: str, context: AdminContext, *, keep_open: bool
) -> Response:
    """Keep the group open on holiday ``day``, or close it again."""
    service = holiday_service(request)
    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        parsed = date.min  # lies in the past, so the service refuses it as holiday_not_found
    try:
        if keep_open:
            await service.keep_open(context.principal, slug, parsed)
        else:
            await service.close_again(context.principal, slug, parsed)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(f"/admin/groups/{slug}/holidays", "saved")


@router.post("/groups/{slug}/holidays/{day}/open", response_class=HTMLResponse)
async def keep_open(
    request: Request, slug: str, day: str, _: SameOrigin, context: ManageGroup
) -> Response:
    """Keep the group open as usual on one holiday."""
    return await _set_open(request, slug, day, context, keep_open=True)


@router.post("/groups/{slug}/holidays/{day}/close", response_class=HTMLResponse)
async def close_again(
    request: Request, slug: str, day: str, _: SameOrigin, context: ManageGroup
) -> Response:
    """Close the group again on one holiday."""
    return await _set_open(request, slug, day, context, keep_open=False)
