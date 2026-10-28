"""Admin routes of maintenance mode (ADR 0038), superuser only.

Every route depends on exactly one ``RequirePermission(system.maintenance)``; switching needs a
fresh login and the Fetch Metadata check. ``system.maintenance`` is in
``MAINTENANCE_WRITABLE``, so these routes keep working while maintenance is on.
``load_maintenance_notice`` puts the maintenance bar on every admin page.

    GET  /admin/maintenance        the state, who switched it and when, the switch
    POST /admin/maintenance/on     switch on (or change the expected end and reason)
    POST /admin/maintenance/off    switch off (refused while MAINTENANCE_MODE keeps it on)
"""

from dataclasses import dataclass
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import (
    AdminContext,
    RequirePermission,
    maintenance_service,
    session_principal,
)
from app.admin.forms import (
    Field,
    FormData,
    done_message,
    options,
    raise_refusal,
    read_form,
    redirect_done,
)
from app.admin.pages import Locale, SameOrigin, admin_moment, with_manual
from app.admin.pages import page as _page
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.domain.maintenance import MaintenanceReason, MaintenanceStatus
from app.web.locale import RequestLocale, resolve_locale

router = APIRouter(prefix="/admin", include_in_schema=False)

View = Annotated[AdminContext, Depends(RequirePermission(Permission.SYSTEM_MAINTENANCE))]
"""Seeing the page."""

Change = Annotated[
    AdminContext, Depends(RequirePermission(Permission.SYSTEM_MAINTENANCE, fresh=True))
]
"""Switching: a fresh login (ADR 0015)."""

URL = "/admin/maintenance"

REASON_LABELS: dict[MaintenanceReason, str] = {
    MaintenanceReason.UPDATE: "admin.maintenance.reason.update",
    MaintenanceReason.RESTORE: "admin.maintenance.reason.restore",
    MaintenanceReason.INTEGRATION: "admin.maintenance.reason.integration",
    MaintenanceReason.INCIDENT: "admin.maintenance.reason.incident",
    MaintenanceReason.OTHER: "admin.maintenance.reason.other",
}
"""Readable label per reason code."""


@dataclass(frozen=True)
class MaintenanceNotice:
    """The maintenance bar as the layout shows it.

    Attributes:
        since: When it was switched on (admin time zone); empty for the environment override.
        expected_end: The expected end (admin time zone), if given.
        environment: Whether ``MAINTENANCE_MODE`` keeps it on.
    """

    since: str
    expected_end: str
    environment: bool


def _notice(
    request: Request, locale: RequestLocale, status: MaintenanceStatus
) -> MaintenanceNotice:
    """Translate a status into the bar's texts."""
    state = status.state
    return MaintenanceNotice(
        since=admin_moment(request, locale, state.since) if state.since else "",
        expected_end=admin_moment(request, locale, state.expected_end)
        if state.expected_end
        else "",
        environment=status.environment,
    )


async def load_maintenance_notice(request: Request) -> None:
    """Put the maintenance bar on ``request.state`` when maintenance is on (all admin pages).

    Read through the service's short cache, so a page costs at most one read per few seconds.
    Only for logged-in admins: the login flow itself never depends on the database.
    """
    if session_principal(request) is None or not hasattr(request.app.state, "maintenance_service"):
        return
    status = await maintenance_service(request).status()
    if status.active:
        request.state.maintenance_notice = _notice(request, resolve_locale(request), status)


def _fields(form: FormData, locale: RequestLocale, status: MaintenanceStatus) -> list[Field]:
    """Return the inputs of the switch-on form: reason and optional expected end."""
    _ = locale.gettext
    current = status.state.reason.value if status.state.reason else MaintenanceReason.OTHER.value
    return [
        Field(
            "reason",
            _("admin.maintenance.reason"),
            "select",
            form.values.get("reason", current),
            hint=_("admin.maintenance.reason_hint"),
            error=form.error_for("reason"),
            options=options((r.value, _(label)) for r, label in REASON_LABELS.items()),
        ),
        Field(
            "expected_end",
            _("admin.maintenance.expected_end"),
            "datetime",
            form.moment_value("expected_end"),
            hint=_("admin.maintenance.expected_end_hint"),
            error=form.error_for("expected_end"),
            required=False,
        ),
    ]


async def _overview(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    form: FormData | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the maintenance page, with the submitted form and its errors when refused."""
    status = await maintenance_service(request).overview(context.principal)
    form = form or FormData({}, locale.gettext)
    state = status.state
    response = _page(
        request,
        locale,
        "admin/maintenance.html",
        context.principal,
        {
            "status": status,
            "since": admin_moment(request, locale, state.since) if state.since else "",
            "expected_end": (
                admin_moment(request, locale, state.expected_end) if state.expected_end else ""
            ),
            "reason": locale.gettext(REASON_LABELS[state.reason]) if state.reason else "",
            "fields": with_manual(request, locale, _fields(form, locale, status)),
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
        },
    )
    response.status_code = status_code
    return response


@router.get("/maintenance", response_class=HTMLResponse)
async def overview(request: Request, locale: Locale, context: View) -> HTMLResponse:
    """Show whether maintenance is on, since when, by whom, and the switch."""
    return await _overview(request, locale, context)


@router.post("/maintenance/on", response_class=HTMLResponse)
async def switch_on(request: Request, locale: Locale, _: SameOrigin, context: Change) -> Response:
    """Switch maintenance on, or change its expected end and reason."""
    form = await read_form(request, locale)
    reason = form.choice("reason", {r.value for r in MaintenanceReason})
    tz = ZoneInfo(request.app.state.settings.admin_timezone)
    expected_end = form.optional_moment("expected_end", tz)
    if form.errors or reason is None:
        return await _overview(request, locale, context, form, 422)
    try:
        await maintenance_service(request).switch_on(
            context.principal, MaintenanceReason(reason), expected_end
        )
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _overview(request, locale, context, form, 422)
    return redirect_done(URL, "maintenance_on")


@router.post("/maintenance/off", response_class=HTMLResponse)
async def switch_off(request: Request, locale: Locale, _: SameOrigin, context: Change) -> Response:
    """Switch maintenance off; the environment override stays on."""
    try:
        await maintenance_service(request).switch_off(context.principal)
    except DomainError as error:
        raise_refusal(error)
        form = FormData({}, locale.gettext)
        form.refusal(error)
        return await _overview(request, locale, context, form, 409)
    return redirect_done(URL, "maintenance_off")
