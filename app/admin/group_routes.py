"""Admin routes for groups and their planning (WP7b).

Every route depends on exactly one ``RequirePermission``; forms and changes are ``fresh``
(login at most ``ADMIN_REAUTH_MINUTES`` old). Changes are POST forms with the Fetch Metadata
check, answered with a redirect (post/redirect/get) and a confirmation on the next page.
The domain (``PlanningService``) authorises again, validates and audits.

    GET  /admin/groups/new                         group.create      form
    POST /admin/groups                             group.create      create
    GET  /admin/groups/{slug}                      group.view        overview and preview
    GET  /admin/groups/{slug}/settings             group.edit        form
    POST /admin/groups/{slug}/settings             group.edit        save
    GET  /admin/groups/{slug}/hours                schedule.manage   weekly hours
    POST /admin/groups/{slug}/hours                schedule.manage   add a window
    POST /admin/groups/{slug}/hours/{id}/delete    schedule.manage   remove a window
    GET  /admin/groups/{slug}/exceptions           schedule.manage   exceptions
    POST /admin/groups/{slug}/exceptions           schedule.manage   add an exception
    POST /admin/groups/{slug}/exceptions/{id}/delete schedule.manage remove an exception
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, can_function
from app.admin.forms import (
    Field,
    FormData,
    Option,
    done_message,
    raise_refusal,
    read_form,
    redirect_done,
)
from app.admin.pages import Locale, SameOrigin, page
from app.admin.timezones import DEFAULT_TIMEZONE, timezone_options
from app.domain.authorization import LEVELS, Level, Permission
from app.domain.errors import DomainError
from app.domain.planning import (
    GroupSettings,
    NewGroup,
    PlanningService,
    PreviewDay,
    Schedule,
    settings_of,
)
from app.domain.scheduling import (
    GroupSummary,
    PhoneMode,
    ScheduleOverride,
    ScheduleRule,
    TimeWindow,
)
from app.i18n import BoundGettext
from app.i18n.dates import (
    format_clock,
    format_day,
    format_day_short,
    format_time_of_day,
    weekday_names,
)
from app.web.authz_labels import PERMISSION_LABELS
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

PHONE_LABELS: dict[PhoneMode, str] = {
    PhoneMode.HIDDEN: "admin.group.phone_hidden",
    PhoneMode.OPTIONAL: "admin.group.phone_optional",
    PhoneMode.REQUIRED: "admin.group.phone_required",
}

NUMBER_LABELS: dict[str, tuple[str, str]] = {
    "slot_minutes": ("admin.field.slot_minutes", "admin.field.slot_minutes_hint"),
    "min_notice_minutes": ("admin.field.min_notice_minutes", "admin.field.min_notice_minutes_hint"),
    "horizon_days": ("admin.field.horizon_days", "admin.field.horizon_days_hint"),
    "dispatch_lead_minutes": (
        "admin.field.dispatch_lead_minutes",
        "admin.field.dispatch_lead_minutes_hint",
    ),
    "not_picked_up_minutes": (
        "admin.field.not_picked_up_minutes",
        "admin.field.not_picked_up_minutes_hint",
    ),
}
"""Label and hint key per numeric setting (literal keys, so the catalogue test sees them)."""

INTEGRATION_LABELS: dict[str, str] = {
    "video_host_email": "admin.field.video_host_email",
    "wxcc_business_address": "admin.field.wxcc_business_address",
    "wxcc_channel": "admin.field.wxcc_channel",
    "wxcc_routing_value": "admin.field.wxcc_routing_value",
}


@dataclass(frozen=True)
class Tab:
    """A section of the group pages, shown only if the matrix allows it for this group."""

    label_key: str
    suffix: str
    permission: Permission
    icon: str


TABS = (
    Tab("admin.tab.overview", "", Permission.GROUP_VIEW, "dashboard"),
    Tab("admin.tab.settings", "/settings", Permission.GROUP_EDIT, "settings"),
    Tab("admin.tab.hours", "/hours", Permission.SCHEDULE_MANAGE, "clock"),
    Tab("admin.tab.exceptions", "/exceptions", Permission.SCHEDULE_MANAGE, "calendar"),
    Tab("admin.tab.reminders", "/reminders", Permission.REMINDERS_MANAGE, "bell"),
    Tab("admin.tab.messages", "/messages", Permission.TEXTS_MANAGE, "mail"),
    Tab("admin.tab.agenda", "/agenda", Permission.BOOKINGS_VIEW, "agenda"),
    Tab("admin.tab.closure", "/closure", Permission.CLOSURE_MANAGE, "alert"),
)


@dataclass(frozen=True)
class TabView:
    """A visible tab."""

    label: str
    href: str
    current: bool
    icon: str


def planning_service(request: Request) -> PlanningService:
    """Return the application's planning service."""
    service: PlanningService = request.app.state.planning_service
    return service


def group_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    template: str,
    extra: dict[str, object],
    status: int = 200,
) -> HTMLResponse:
    """Render a group page with header, the tabs the matrix allows, and any confirmation."""
    summary = context.group
    can = can_function(context.principal)
    base = f"/admin/groups/{summary.group.slug}"
    tabs = [
        TabView(
            locale.gettext(t.label_key),
            base + t.suffix,
            request.url.path == base + t.suffix
            or bool(t.suffix and request.url.path.startswith(f"{base}{t.suffix}/")),
            t.icon,
        )
        for t in TABS
        if can(t.permission, summary)
    ]
    response = page(
        request,
        locale,
        template,
        context.principal,
        {"summary": summary, "tabs": tabs, "done": done_message(request, locale.gettext), **extra},
    )
    response.status_code = status
    return response


# --- new group ------------------------------------------------------------------------------


def _timezone_field(zone: str, form: FormData, _: BoundGettext) -> Field:
    """Return the time-zone select with ``zone`` selected."""
    return Field(
        "timezone",
        _("admin.field.timezone"),
        "select",
        zone,
        hint=_("admin.field.timezone_hint"),
        error=form.error_for("timezone"),
        options=timezone_options(zone),
    )


def _new_group_fields(
    form: FormData, organisations: Sequence[Option], _: BoundGettext
) -> list[Field]:
    """Return the inputs of the new-group form."""
    return [
        Field(
            "organisation",
            _("admin.field.organisation"),
            "select",
            form.text("organisation") or (organisations[0].value if organisations else ""),
            error=form.error_for("organisation"),
            options=tuple(organisations),
        ),
        Field("name", _("admin.field.name"), value=form.text("name"), error=form.error_for("name")),
        Field(
            "slug",
            _("admin.field.slug"),
            value=form.text("slug"),
            hint=_("admin.field.slug_hint"),
            error=form.error_for("slug"),
            attributes={"autocapitalize": "none", "spellcheck": "false"},
        ),
        _timezone_field(form.text("timezone") or DEFAULT_TIMEZONE, form, _),
        Field(
            "slot_minutes",
            _("admin.field.slot_minutes"),
            "number",
            form.text("slot_minutes") or "30",
            hint=_("admin.field.slot_minutes_hint"),
            error=form.error_for("slot_minutes"),
            attributes={"min": "5", "max": "480", "step": "5", "inputmode": "numeric"},
        ),
    ]


async def _new_group_response(
    request: Request, locale: RequestLocale, context: AdminContext, form: FormData, status: int
) -> HTMLResponse:
    """Render the new-group form."""
    organisations = await planning_service(request).organisations_for_new_group(context.principal)
    options = [Option(o.slug, o.name) for o in organisations]
    response = page(
        request,
        locale,
        "admin/group_new.html",
        context.principal,
        {"fields": _new_group_fields(form, options, locale.gettext), "errors": form.errors},
    )
    response.status_code = status
    return response


@router.get("/groups/new", response_class=HTMLResponse)
async def new_group(
    request: Request,
    locale: Locale,
    context: Annotated[
        AdminContext, Depends(RequirePermission(Permission.GROUP_CREATE, fresh=True))
    ],
) -> HTMLResponse:
    """Show the form for a new group."""
    return await _new_group_response(request, locale, context, FormData({}, locale.gettext), 200)


@router.post("/groups", response_class=HTMLResponse)
async def create_group(
    request: Request,
    locale: Locale,
    _: SameOrigin,
    context: Annotated[
        AdminContext, Depends(RequirePermission(Permission.GROUP_CREATE, fresh=True))
    ],
) -> Response:
    """Create a group, then continue with its opening hours."""
    form = await read_form(request, locale)
    minutes = form.integer("slot_minutes")
    if not form.errors and minutes is not None:
        new = NewGroup(
            form.text("organisation"),
            form.text("name"),
            form.text("slug"),
            form.text("timezone"),
            minutes,
        )
        try:
            group = await planning_service(request).create_group(context.principal, new)
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return redirect_done(f"/admin/groups/{group.slug}", "created")
    return await _new_group_response(request, locale, context, form, 422)


# --- overview -------------------------------------------------------------------------------


@dataclass(frozen=True)
class DayView:
    """One weekday (0 = Monday) with its opening windows."""

    weekday: int
    name: str
    windows: list["WindowView"]


@dataclass(frozen=True)
class WindowView:
    """An opening window as shown in the schedule."""

    id: int | None
    time: str
    capacity: str
    validity: str | None


@dataclass(frozen=True)
class ExceptionView:
    """An exception as shown in the list."""

    id: int | None
    day: str
    closed: bool
    time: str | None
    capacity: str | None


@dataclass(frozen=True)
class PreviewView:
    """One day of the preview of citizens' slots."""

    day: str
    summary: str


def _clock_text(window: TimeWindow, locale: str) -> str:
    """Return ``09:00–12:00`` in the system clock style."""
    return f"{format_time_of_day(window.opens, locale)}–{format_time_of_day(window.closes, locale)}"


def _week(schedule: Schedule, locale: RequestLocale) -> list[DayView]:
    """Group the weekly rules per weekday, Monday first."""
    _ = locale.gettext
    names = weekday_names(locale.code)
    days = []
    for weekday, name in enumerate(names):
        windows = []
        for rule in (r for r in schedule.rules if r.weekday == weekday):
            validity = None
            if rule.valid_from or rule.valid_to:
                validity = _(
                    "admin.hours.validity",
                    start=format_day_short(rule.valid_from, locale.code)
                    if rule.valid_from
                    else "…",
                    end=format_day_short(rule.valid_to, locale.code) if rule.valid_to else "…",
                )
            windows.append(
                WindowView(
                    rule.id,
                    _clock_text(rule.window, locale.code),
                    _("admin.hours.capacity", count=rule.window.capacity),
                    validity,
                )
            )
        days.append(DayView(weekday, name, windows))
    return days


def _exceptions(schedule: Schedule, locale: RequestLocale) -> list[ExceptionView]:
    """Return the upcoming exceptions for display."""
    _ = locale.gettext
    return [
        ExceptionView(
            o.id,
            format_day(o.day, locale.code),
            o.window is None,
            _clock_text(o.window, locale.code) if o.window else None,
            _("admin.hours.capacity", count=o.window.capacity) if o.window else None,
        )
        for o in schedule.overrides
    ]


def _preview(
    days: list[PreviewDay], summary: GroupSummary, locale: RequestLocale
) -> list[PreviewView]:
    """Summarise the offered slots per day."""
    _ = locale.gettext
    tz = summary.group.tz
    return [
        PreviewView(
            format_day_short(d.day, locale.code),
            _(
                "admin.preview.day",
                count=len(d.slots),
                first=format_clock(d.slots[0].start, tz, locale.code),
                last=format_clock(d.slots[-1].start, tz, locale.code),
                capacity=max(s.capacity for s in d.slots),
            ),
        )
        for d in days
    ]


@router.get("/groups/{slug}", response_class=HTMLResponse)
async def group(
    request: Request,
    locale: Locale,
    slug: str,
    context: Annotated[
        AdminContext, Depends(RequirePermission(Permission.GROUP_VIEW, group_param="slug"))
    ],
) -> HTMLResponse:
    """Show one group: settings, week, exceptions, a preview and the admin's rights."""
    _ = locale.gettext
    service = planning_service(request)
    schedule = await service.schedule(context.principal, slug)
    preview = await service.preview(context.principal, slug)
    summary = context.group
    can = can_function(context.principal)
    group_rights = [
        _(PERMISSION_LABELS[p]) for p in Permission if LEVELS[p] is Level.GROUP and can(p, summary)
    ]
    return group_page(
        request,
        locale,
        context,
        "admin/group.html",
        {
            "phone_label": _(PHONE_LABELS[summary.group.phone_mode]),
            "group_rights": group_rights,
            "week": _week(schedule, locale),
            "exceptions": _exceptions(schedule, locale),
            "preview": _preview(preview, summary, locale),
        },
    )


# --- settings -------------------------------------------------------------------------------


def _settings_fields(
    form: FormData, current: GroupSettings, _: BoundGettext
) -> tuple[list[Field], list[Field]]:
    """Return the planning and the integration inputs, pre-filled with the current values."""

    def value(name: str, fallback: object) -> str:
        return form.values.get(name, "" if fallback is None else str(fallback))

    def number(name: str, fallback: int, attributes: dict[str, str]) -> Field:
        label_key, hint_key = NUMBER_LABELS[name]
        return Field(
            name,
            _(label_key),
            "number",
            value(name, fallback),
            hint=_(hint_key),
            error=form.error_for(name),
            attributes={"inputmode": "numeric", **attributes},
        )

    def text(name: str, fallback: str | None, hint_key: str | None = None) -> Field:
        return Field(
            name,
            _(INTEGRATION_LABELS[name]),
            value=value(name, fallback),
            hint=_(hint_key) if hint_key else None,
            error=form.error_for(name),
            required=False,
            attributes={"spellcheck": "false", "autocapitalize": "none"},
        )

    phone_options = tuple(Option(mode.value, _(PHONE_LABELS[mode])) for mode in PhoneMode)
    planning = [
        Field(
            "name",
            _("admin.field.name"),
            value=value("name", current.name),
            error=form.error_for("name"),
        ),
        _timezone_field(value("timezone", current.timezone), form, _),
        number("slot_minutes", current.slot_minutes, {"min": "5", "max": "480", "step": "5"}),
        number("min_notice_minutes", current.min_notice_minutes, {"min": "0", "step": "5"}),
        number("horizon_days", current.horizon_days, {"min": "1", "max": "366"}),
        number("dispatch_lead_minutes", current.dispatch_lead_minutes, {"min": "0", "max": "60"}),
        Field(
            "phone_mode",
            _("admin.field.phone_mode"),
            "select",
            value("phone_mode", current.phone_mode.value),
            error=form.error_for("phone_mode"),
            options=phone_options,
        ),
    ]
    yes_no = (Option("yes", _("admin.yes")), Option("no", _("admin.no")))
    integrations = [
        text("video_host_email", current.video_host_email, "admin.field.video_host_email_hint"),
        text("wxcc_business_address", current.wxcc_business_address),
        text("wxcc_channel", current.wxcc_channel),
        text(
            "wxcc_routing_value", current.wxcc_routing_value, "admin.field.wxcc_routing_value_hint"
        ),
        Field(
            "direct_call_enabled",
            _("admin.field.direct_call_enabled"),
            "select",
            value("direct_call_enabled", "yes" if current.direct_call_enabled else "no"),
            hint=_("admin.field.direct_call_enabled_hint"),
            error=form.error_for("direct_call_enabled"),
            options=yes_no,
        ),
        number("not_picked_up_minutes", current.not_picked_up_minutes, {"min": "1", "max": "120"}),
    ]
    return planning, integrations


def _settings_response(
    request: Request, locale: RequestLocale, context: AdminContext, form: FormData, status: int
) -> HTMLResponse:
    """Render the settings form."""
    current = settings_of(context.group.group)
    planning, integrations = _settings_fields(form, current, locale.gettext)
    return group_page(
        request,
        locale,
        context,
        "admin/group_settings.html",
        {
            "fields": planning,
            "integration_fields": integrations,
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/settings", response_class=HTMLResponse)
async def settings(
    request: Request,
    locale: Locale,
    slug: str,
    context: Annotated[
        AdminContext,
        Depends(RequirePermission(Permission.GROUP_EDIT, group_param="slug", fresh=True)),
    ],
) -> HTMLResponse:
    """Show the group's settings form."""
    return _settings_response(request, locale, context, FormData({}, locale.gettext), 200)


@router.post("/groups/{slug}/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    locale: Locale,
    slug: str,
    _: SameOrigin,
    context: Annotated[
        AdminContext,
        Depends(RequirePermission(Permission.GROUP_EDIT, group_param="slug", fresh=True)),
    ],
) -> Response:
    """Save the group's settings."""
    form = await read_form(request, locale)
    numbers = {name: form.integer(name) for name in NUMBER_LABELS}
    phone = form.choice("phone_mode", {mode.value for mode in PhoneMode})
    direct = form.choice("direct_call_enabled", {"yes", "no"})
    if not form.errors and phone is not None and direct is not None:
        new = GroupSettings(
            name=form.text("name"),
            timezone=form.text("timezone"),
            phone_mode=PhoneMode(phone),
            video_host_email=form.optional_text("video_host_email"),
            wxcc_business_address=form.optional_text("wxcc_business_address"),
            wxcc_channel=form.optional_text("wxcc_channel"),
            wxcc_routing_value=form.optional_text("wxcc_routing_value"),
            direct_call_enabled=direct == "yes",
            **{name: value for name, value in numbers.items() if value is not None},
        )
        try:
            changed = await planning_service(request).update_settings(context.principal, slug, new)
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return redirect_done(
                f"/admin/groups/{slug}/settings", "saved" if changed else "unchanged"
            )
    return _settings_response(request, locale, context, form, 422)


# --- opening hours --------------------------------------------------------------------------

WEEKDAYS = frozenset(str(i) for i in range(7))


def _hours_fields(form: FormData, locale: RequestLocale) -> list[Field]:
    """Return the inputs of the add-window form."""
    _ = locale.gettext
    days = tuple(Option(str(i), name) for i, name in enumerate(weekday_names(locale.code)))
    return [
        Field(
            "weekday",
            _("admin.field.weekday"),
            "select",
            form.text("weekday") or "0",
            error=form.error_for("weekday"),
            options=days,
        ),
        Field(
            "opens",
            _("admin.field.opens"),
            "time",
            form.text("opens") or "09:00",
            error=form.error_for("opens"),
            attributes={"step": "300"},
        ),
        Field(
            "closes",
            _("admin.field.closes"),
            "time",
            form.text("closes") or "12:00",
            error=form.error_for("closes"),
            attributes={"step": "300"},
        ),
        Field(
            "capacity",
            _("admin.field.capacity"),
            "number",
            form.text("capacity") or "1",
            hint=_("admin.field.capacity_hint"),
            error=form.error_for("capacity"),
            attributes={"min": "1", "max": "1000", "inputmode": "numeric"},
        ),
        Field(
            "valid_from",
            _("admin.field.valid_from"),
            "date",
            form.text("valid_from"),
            error=form.error_for("valid_from"),
            required=False,
        ),
        Field(
            "valid_to",
            _("admin.field.valid_to"),
            "date",
            form.text("valid_to"),
            hint=_("admin.field.valid_hint"),
            error=form.error_for("valid_to"),
            required=False,
        ),
    ]


async def _hours_response(
    request: Request, locale: RequestLocale, context: AdminContext, form: FormData, status: int
) -> HTMLResponse:
    """Render the weekly hours with the add-window form."""
    schedule = await planning_service(request).schedule(context.principal, context.group.group.slug)
    return group_page(
        request,
        locale,
        context,
        "admin/group_hours.html",
        {
            "week": _week(schedule, locale),
            "fields": _hours_fields(form, locale),
            "errors": form.errors,
        },
        status,
    )


ManageGroup = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.SCHEDULE_MANAGE, group_param="slug", fresh=True)),
]


@router.get("/groups/{slug}/hours", response_class=HTMLResponse)
async def hours(request: Request, locale: Locale, slug: str, context: ManageGroup) -> HTMLResponse:
    """Show the weekly opening hours; ``?weekday=`` preselects the day of the add form."""
    weekday = request.query_params.get("weekday", "")
    values = {"weekday": weekday} if weekday in WEEKDAYS else {}
    return await _hours_response(request, locale, context, FormData(values, locale.gettext), 200)


@router.post("/groups/{slug}/hours", response_class=HTMLResponse)
async def add_hours(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageGroup
) -> Response:
    """Add a weekly opening window."""
    form = await read_form(request, locale)
    weekday = form.choice("weekday", WEEKDAYS)
    opens, closes = form.clock("opens"), form.clock("closes")
    capacity = form.integer("capacity")
    valid_from, valid_to = form.optional_day("valid_from"), form.optional_day("valid_to")
    if not form.errors and weekday and opens and closes and capacity is not None:
        try:
            rule = ScheduleRule(
                int(weekday), TimeWindow(opens, closes, capacity), valid_from, valid_to
            )
            await planning_service(request).add_rule(context.principal, slug, rule)
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return redirect_done(f"/admin/groups/{slug}/hours", "added")
    return await _hours_response(request, locale, context, form, 422)


@router.post("/groups/{slug}/hours/{rule_id}/delete", response_class=HTMLResponse)
async def remove_hours(
    request: Request, slug: str, rule_id: int, _: SameOrigin, context: ManageGroup
) -> Response:
    """Remove a weekly opening window."""
    try:
        await planning_service(request).remove_rule(context.principal, slug, rule_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(f"/admin/groups/{slug}/hours", "removed")


# --- exceptions -----------------------------------------------------------------------------


def _exception_fields(form: FormData, locale: RequestLocale) -> list[Field]:
    """Return the inputs of the add-exception form (the kind is a separate radio group)."""
    _ = locale.gettext
    return [
        Field("day", _("admin.field.day"), "date", form.text("day"), error=form.error_for("day")),
        Field(
            "opens",
            _("admin.field.opens"),
            "time",
            form.text("opens"),
            hint=_("admin.field.only_with_hours"),
            error=form.error_for("opens"),
            required=False,
            attributes={"step": "300"},
        ),
        Field(
            "closes",
            _("admin.field.closes"),
            "time",
            form.text("closes"),
            error=form.error_for("closes"),
            required=False,
            attributes={"step": "300"},
        ),
        Field(
            "capacity",
            _("admin.field.capacity"),
            "number",
            form.text("capacity"),
            hint=_("admin.field.capacity_hint"),
            error=form.error_for("capacity"),
            required=False,
            attributes={"min": "1", "max": "1000", "inputmode": "numeric"},
        ),
    ]


async def _exceptions_response(
    request: Request, locale: RequestLocale, context: AdminContext, form: FormData, status: int
) -> HTMLResponse:
    """Render the exceptions with the add-exception form."""
    schedule = await planning_service(request).schedule(context.principal, context.group.group.slug)
    return group_page(
        request,
        locale,
        context,
        "admin/group_exceptions.html",
        {
            "exceptions": _exceptions(schedule, locale),
            "fields": _exception_fields(form, locale),
            "kind": form.text("kind") or "closed",
            "kind_error": form.error_for("kind"),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/exceptions", response_class=HTMLResponse)
async def exceptions(
    request: Request, locale: Locale, slug: str, context: ManageGroup
) -> HTMLResponse:
    """Show the upcoming exceptions."""
    return await _exceptions_response(request, locale, context, FormData({}, locale.gettext), 200)


@router.post("/groups/{slug}/exceptions", response_class=HTMLResponse)
async def add_exception(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageGroup
) -> Response:
    """Close a date or give it its own hours."""
    form = await read_form(request, locale)
    day = form.day("day")
    kind = form.choice("kind", {"closed", "hours"})
    window = None
    if kind == "hours":
        opens, closes, capacity = (
            form.clock("opens"),
            form.clock("closes"),
            form.integer("capacity"),
        )
        if opens and closes and capacity is not None:
            try:
                window = TimeWindow(opens, closes, capacity)
            except DomainError as error:
                form.refusal(error)
    if not form.errors and day is not None:
        try:
            await planning_service(request).add_override(
                context.principal, slug, ScheduleOverride(day, window)
            )
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return redirect_done(f"/admin/groups/{slug}/exceptions", "added")
    return await _exceptions_response(request, locale, context, form, 422)


@router.post("/groups/{slug}/exceptions/{override_id}/delete", response_class=HTMLResponse)
async def remove_exception(
    request: Request, slug: str, override_id: int, _: SameOrigin, context: ManageGroup
) -> Response:
    """Remove an exception."""
    try:
        await planning_service(request).remove_override(context.principal, slug, override_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(f"/admin/groups/{slug}/exceptions", "removed")
