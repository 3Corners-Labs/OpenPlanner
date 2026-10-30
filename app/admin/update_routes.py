"""Admin routes for updates of the installation (ADR 0029), superuser only.

Every route depends on exactly one ``RequirePermission(system.update)``; requests need a fresh
login and the Fetch Metadata check. The routes only store requests: the host updater carries
them out and reports back. ``load_update_notice`` puts the banner on every admin page but this
one, which shows the open request's steps itself.

    GET  /admin/updates                         running version, latest release, history
    GET  /admin/updates/progress?seen=KEY       the steps of the open request, as a fragment
                                                (204 while unchanged; ADR 0048)
    POST /admin/updates/update                  request the latest release
    POST /admin/updates/restore                 request the version the last update replaced
    POST /admin/updates/requests/{id}/cancel    withdraw a request not yet claimed
    POST /admin/updates/snooze                  hide the banner for 60 minutes (this session)
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission, session_principal
from app.admin.forms import done_message, raise_refusal, read_form, redirect_done, safe_next
from app.admin.pages import Locale, SameOrigin, admin_moment, templates
from app.admin.pages import page as _page
from app.domain.audit import AuditEntry
from app.domain.authorization import Permission
from app.domain.errors import DomainError, ErrorCode
from app.domain.update_service import UpdateNotice, UpdateOverview, UpdateService
from app.domain.updates import (
    SNOOZE,
    UpdateRequest,
    UpdateStatus,
    Version,
    detail_fields,
    detail_key,
    progress,
)
from app.i18n import BoundGettext
from app.web.errors import form_errors, status_for
from app.web.locale import RequestLocale, resolve_locale
from app.web.templates import render
from app.web.update_labels import (
    ACTION_LABELS,
    ACTOR_LABELS,
    HISTORY_LABELS,
    STATUS_LABELS,
    STEP_LABELS,
    STEP_STATE_LABELS,
    reason_label,
)

router = APIRouter(prefix="/admin", include_in_schema=False)

View = Annotated[AdminContext, Depends(RequirePermission(Permission.SYSTEM_UPDATE))]
"""Seeing the page and snoozing the banner."""

Change = Annotated[AdminContext, Depends(RequirePermission(Permission.SYSTEM_UPDATE, fresh=True))]
"""Every request: a fresh login (ADR 0015)."""

SNOOZE_KEY = "update_snoozed_until"
"""Session key of the banner's snooze (an ISO timestamp; no personal data)."""

URL = "/admin/updates"
PROGRESS_URL = "/admin/updates/progress"


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


@dataclass(frozen=True)
class StepView:
    """One line of the progress list, translated."""

    label: str
    state: str
    state_label: str
    since: str


@dataclass(frozen=True)
class ProgressView:
    """The progress fragment (ADR 0048): the open request's steps, or how it ended.

    Attributes:
        request: The open request, or the latest one once it ended.
        key: Changes whenever what the fragment shows changes; the poller sends it back as
            ``seen`` and gets 204 while it is the same.
        polling: Whether the page should keep asking (the request is still open).
        steps: The steps with their state; empty once it ended.
        status: One line for the live region: the current step, or the outcome.
        reason: The updater's reason for a rollback or failure, translated.
        unclaimed: Whether no updater seems to pick the request up.
    """

    request: UpdateRequest
    key: str
    polling: bool
    steps: list[StepView]
    status: str
    reason: str
    unclaimed: bool


def _step_label(_: BoundGettext, request: UpdateRequest) -> str:
    """Return the label of the step an open request is at."""
    current = next(item for item in progress(request) if item.state == "current")
    return _(STEP_LABELS[current.step], version=str(request.target))


def _progress(
    request: Request, locale: RequestLocale, found: UpdateOverview
) -> ProgressView | None:
    """Build the progress fragment from the overview; ``None`` before any request."""
    _ = locale.gettext
    shown = found.open or (found.history[0] if found.history else None)
    if shown is None:
        return None
    steps = [
        StepView(
            label=_(STEP_LABELS[item.step], version=str(shown.target)),
            state=item.state,
            state_label=_(STEP_STATE_LABELS[item.state]),
            since=_("admin.updates.step.since", time=admin_moment(request, locale, item.since))
            if item.since
            else "",
        )
        for item in progress(shown)
    ]
    current = next((step for step in steps if step.state == "current"), None)
    status = (
        _("admin.updates.progress.status", step=current.label)
        if current
        else _(
            "admin.updates.progress.ended",
            version=str(shown.target),
            status=_(STATUS_LABELS[shown.status]),
        )
    )
    return ProgressView(
        request=shown,
        key=f"{shown.id}.{shown.status}.{shown.step or ''}.{int(found.unclaimed)}",
        polling=shown.is_open,
        steps=steps,
        status=status,
        reason=_(reason_label(shown.reason)) if shown.reason else "",
        unclaimed=found.unclaimed,
    )


def _banner(notice: UpdateNotice, current: str, _: BoundGettext, path: str) -> Banner:
    """Choose what the banner says: a running request, a problem, a release, or success."""
    outcome = notice.outcome
    if notice.open is not None:
        version = str(notice.open.target)
        if notice.open.status is UpdateStatus.REQUESTED:
            text = _("admin.updates.banner.progress", version=version)
        else:
            text = _("admin.updates.banner.step", version=version, step=_step_label(_, notice.open))
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
    service), while the banner is snoozed in this session, and on the updates pages
    themselves, which show all of it.
    """
    principal = session_principal(request)
    if (
        principal is None
        or not hasattr(request.app.state, "update_service")
        or request.url.path.startswith(URL)
    ):
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


@dataclass(frozen=True)
class HistoryView:
    """One line of the history: an audit event of the updates (ADR 0067 point 5).

    Attributes:
        at: When, in the admin time zone.
        by: Who: the staff member's name, or "system", "the updater" or "the host".
        what: What happened, from the detail key.
        from_version: The version before; empty when the event predates ADR 0067.
        to_version: The version after.
        reason: The updater's reason, for a failure or rollback.
        problem: Whether the line is a failure or rollback.
    """

    at: str
    by: str
    what: str
    from_version: str
    to_version: str
    reason: str
    problem: bool


def _actor(entry: AuditEntry, _: BoundGettext) -> str:
    """Name who acted: a staff member's display name, a known machine actor, or the subject."""
    actor = entry.event.actor
    if actor in ACTOR_LABELS:
        return _(ACTOR_LABELS[actor])
    return entry.actor_name or actor


def _audit_history(
    request: Request, locale: RequestLocale, entries: list[AuditEntry]
) -> list[HistoryView]:
    """Translate the audit entries of the updates for the history table."""
    _ = locale.gettext
    views = []
    for entry in entries:
        key = detail_key(entry.event.detail)
        fields = detail_fields(entry.event.detail)
        views.append(
            HistoryView(
                at=admin_moment(request, locale, entry.event.at),
                by=_actor(entry, _),
                what=_(HISTORY_LABELS.get(key, "admin.updates.history.other")),
                from_version=fields.get("from", ""),
                to_version=fields.get("to", ""),
                reason=_(reason_label(entry.event.reason)) if entry.event.reason else "",
                problem=key.endswith((".failed", ".rolled_back")),
            )
        )
    return views


@dataclass(frozen=True)
class CommitView:
    """The commit this image was built from (ADR 0052).

    Attributes:
        short: The first 12 characters, as the page shows them.
        url: The commit on GitHub; empty when ``UPDATE_REPOSITORY`` is not set.
    """

    short: str
    url: str


def _commit(request: Request) -> CommitView | None:
    """Return the running image's commit; ``None`` for a build without ``APP_COMMIT``."""
    settings = request.app.state.settings
    sha: str = settings.app_commit
    if not sha:
        return None
    repository = settings.update_repository
    url = f"https://github.com/{repository}/commit/{sha}" if repository else ""
    return CommitView(sha[:12], url)


async def _overview(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    error: DomainError | None = None,
) -> HTMLResponse:
    """Render the updates page, with the refusal of a request when there is one."""
    service = update_service(request)
    found = await service.overview(context.principal)
    entries = await service.history(context.principal)
    check = found.check
    live = _progress(request, locale, found) if found.open else None
    response = _page(
        request,
        locale,
        "admin/updates.html",
        context.principal,
        {
            "overview": found,
            "commit": _commit(request),
            "checked_at": admin_moment(request, locale, check.checked_at) if check else None,
            "check_error": check.error if check else "",
            "history": _audit_history(request, locale, entries),
            "offered": [
                {
                    "version": str(item.version),
                    "url": item.url,
                    "candidate": item.version.is_candidate,
                    "published": (
                        admin_moment(request, locale, item.published_at)
                        if item.published_at
                        else ""
                    ),
                    "notes": item.notes.splitlines(),
                }
                for item in found.offered
            ],
            "outside_change": (
                admin_moment(request, locale, found.outside_change.since)
                if found.outside_change
                else None
            ),
            "outside_version": found.outside_change.version if found.outside_change else "",
            "errors": form_errors(error, locale.gettext) if error else [],
            "done": done_message(request, locale.gettext),
            "progress": live,
        },
        scripts=live is not None,
    )
    if error is not None:
        response.status_code = status_for(error)
    return response


@router.get("/updates", response_class=HTMLResponse)
async def overview(request: Request, locale: Locale, context: View) -> HTMLResponse:
    """Show the running version, the latest release, the possible actions and the history."""
    return await _overview(request, locale, context)


# A fragment, not a page: no ``response_class=HTMLResponse``, so it needs no manual topic.
@router.get("/updates/progress")
async def progress_fragment(
    request: Request,
    locale: Locale,
    context: View,
    seen: Annotated[str, Query(max_length=64)] = "",
) -> Response:
    """Return the progress fragment for the page's poller; 204 while ``seen`` is current.

    Once the request has ended the fragment carries no poll URL, so the poller stops.
    """
    found = await update_service(request).overview(context.principal)
    view = _progress(request, locale, found)
    if view is not None and view.key == seen:
        return Response(status_code=204, headers={"cache-control": "no-store"})
    return render(templates, request, "admin/_update_progress.html", locale, {"progress": view})


@router.post("/updates/update", response_class=HTMLResponse)
async def request_update(
    request: Request, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Ask the host updater to install the chosen version, or the newest one offered."""
    form = await read_form(request, locale)
    raw = form.text("target")
    target = Version.parse(raw) if raw else None
    try:
        if raw and target is None:
            raise DomainError(ErrorCode.UPDATE_NOT_AVAILABLE)
        await update_service(request).request_update(context.principal, target)
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
