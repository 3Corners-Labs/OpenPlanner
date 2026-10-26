"""Admin routes for updates of the installation (ADR 0028), superuser only.

Every route depends on exactly one ``RequirePermission(system.update)``; requests need a fresh
login and the Fetch Metadata check. The routes only store requests: the host updater carries
them out and reports back. ``load_update_notice`` puts the banner on every admin page.

    GET  /admin/updates                         running version, latest release, history
    POST /admin/updates/update                  request the latest release
    POST /admin/updates/restore                 request the version the last update replaced
    POST /admin/updates/requests/{id}/cancel    withdraw a request not yet claimed
    POST /admin/updates/snooze                  hide the banner for 60 minutes (this session)
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission, session_principal
from app.admin.forms import done_message, raise_refusal, read_form, redirect_done, safe_next
from app.admin.pages import Locale, SameOrigin, admin_moment
from app.admin.pages import page as _page
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.domain.update_service import UpdateNotice, UpdateService
from app.domain.updates import SNOOZE, UpdateRequest, UpdateStatus
from app.i18n import BoundGettext
from app.web.errors import form_errors, status_for
from app.web.locale import RequestLocale, resolve_locale
from app.web.update_labels import ACTION_LABELS, STATUS_LABELS, reason_label

router = APIRouter(prefix="/admin", include_in_schema=False)

View = Annotated[AdminContext, Depends(RequirePermission(Permission.SYSTEM_UPDATE))]
"""Seeing the page and snoozing the banner."""

Change = Annotated[AdminContext, Depends(RequirePermission(Permission.SYSTEM_UPDATE, fresh=True))]
"""Every request: a fresh login (ADR 0015)."""

SNOOZE_KEY = "update_snoozed_until"
"""Session key of the banner's snooze (an ISO timestamp; no personal data)."""

URL = "/admin/updates"


def update_service(request: Request) -> UpdateService:
    """Return the application's update service."""
    service: UpdateService = request.app.state.update_service
    return service


@dataclass(frozen=True)
class Banner:
    """The update banner as the layout shows it.

    Attributes:
        kind: ``available``, ``progress``, ``success`` or ``warning`` (the CSS modifier).
        text: The translated message.
        snooze: Whether to offer "remind me in 60 minutes".
        next_path: The page to return to after snoozing.
    """

    kind: str
    text: str
    snooze: bool
    next_path: str


@dataclass(frozen=True)
class RequestView:
    """One request of the history table."""

    request: UpdateRequest
    action: str
    status: str
    reason: str
    requested_at: str
    finished_at: str


def _banner(notice: UpdateNotice, current: str, _: BoundGettext, path: str) -> Banner:
    """Choose what the banner says: a running request, a problem, a release, or success."""
    outcome = notice.outcome
    if notice.open is not None:
        text = _("admin.updates.banner.progress", version=str(notice.open.target))
        return Banner("progress", text, False, path)
    if outcome is not None and outcome.status is not UpdateStatus.SUCCEEDED:
        text = _(
            "admin.updates.banner.problem",
            version=str(outcome.target),
            status=_(STATUS_LABELS[outcome.status]),
        )
        return Banner("warning", text, True, path)
    if notice.available is not None:
        text = _(
            "admin.updates.banner.available",
            version=str(notice.available.version),
            current=current,
        )
        return Banner("available", text, True, path)
    if outcome is None:  # pragma: no cover - the service returns no empty notice
        raise ValueError("empty notice")
    return Banner("success", _("admin.updates.banner.done", version=current), True, path)


async def load_update_notice(request: Request) -> None:
    """Put the update banner for the logged-in admin on ``request.state`` (all admin pages).

    Nothing for visitors, for admins without ``system.update`` (decided by the matrix in the
    service) and while the banner is snoozed in this session.
    """
    principal = session_principal(request)
    if principal is None or not hasattr(request.app.state, "update_service"):
        return
    now = request.app.state.clock.now()
    until = request.session.get(SNOOZE_KEY)
    if isinstance(until, str) and now < datetime.fromisoformat(until):
        return
    service = update_service(request)
    notice = await service.notice(principal)
    if notice is None:
        return
    locale = resolve_locale(request)
    current = str(service.current) if service.current else "?"
    request.state.update_notice = _banner(notice, current, locale.gettext, request.url.path)


def _history(
    request: Request, locale: RequestLocale, history: list[UpdateRequest]
) -> list[RequestView]:
    """Translate the request history for the table."""
    _ = locale.gettext
    return [
        RequestView(
            request=item,
            action=_(ACTION_LABELS[item.action]),
            status=_(STATUS_LABELS[item.status]),
            reason=_(reason_label(item.reason)) if item.reason else "",
            requested_at=admin_moment(request, locale, item.requested_at),
            finished_at=admin_moment(request, locale, item.finished_at) if item.finished_at else "",
        )
        for item in history
    ]


async def _overview(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    error: DomainError | None = None,
) -> HTMLResponse:
    """Render the updates page, with the refusal of a request when there is one."""
    found = await update_service(request).overview(context.principal)
    check = found.check
    response = _page(
        request,
        locale,
        "admin/updates.html",
        context.principal,
        {
            "overview": found,
            "checked_at": admin_moment(request, locale, check.checked_at) if check else None,
            "check_error": check.error if check else "",
            "history": _history(request, locale, found.history),
            "errors": form_errors(error, locale.gettext) if error else [],
            "done": done_message(request, locale.gettext),
        },
    )
    if error is not None:
        response.status_code = status_for(error)
    return response


@router.get("/updates", response_class=HTMLResponse)
async def overview(request: Request, locale: Locale, context: View) -> HTMLResponse:
    """Show the running version, the latest release, the possible actions and the history."""
    return await _overview(request, locale, context)


@router.post("/updates/update", response_class=HTMLResponse)
async def request_update(
    request: Request, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Ask the host updater to install the latest release."""
    try:
        await update_service(request).request_update(context.principal)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, error)
    return redirect_done(URL, "update_requested")


@router.post("/updates/restore", response_class=HTMLResponse)
async def request_restore(
    request: Request, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Ask the host updater to go back to the version the latest update replaced."""
    try:
        await update_service(request).request_restore(context.principal)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, error)
    return redirect_done(URL, "restore_requested")


@router.post("/updates/requests/{request_id}/cancel", response_class=HTMLResponse)
async def cancel_request(
    request: Request, locale: Locale, request_id: int, _: SameOrigin, context: Change
) -> Response:
    """Withdraw a request the host updater has not claimed yet."""
    try:
        await update_service(request).cancel(context.principal, request_id)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, error)
    return redirect_done(URL, "request_cancelled")


@router.post("/updates/snooze", response_class=HTMLResponse)
async def snooze(request: Request, locale: Locale, _: SameOrigin, context: View) -> Response:
    """Hide the banner for 60 minutes in this session and return to the page it was on."""
    form = await read_form(request, locale)
    until = request.app.state.clock.now() + SNOOZE
    request.session[SNOOZE_KEY] = until.isoformat()
    return RedirectResponse(safe_next(form.text("next")), status_code=303)
