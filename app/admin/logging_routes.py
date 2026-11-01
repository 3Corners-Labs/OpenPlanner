"""The *Log destinations* screen: where copies of the log go (ADR 0085 step 4).

Platform → Log destinations. Everyone with ``logging.view`` (superuser, auditor) sees the
destinations and the status each process reports; every change needs ``logging.manage``
(locked to the superuser) and a fresh login. Changes are POST forms with the Fetch Metadata
check. The credential is write-only: its field is always empty and a blank field keeps the
stored one (ADR 0008). The page shows reasons and status codes, never a platform's own text
and never an address with a secret in it (the domain refuses one).

*Send test line* delivers one real line and shows the verdict at once; the verdict is audited.

    GET  /admin/logging                              logging.view
    GET  /admin/logging/new                          logging.manage
    POST /admin/logging                              logging.manage
    GET  /admin/logging/{destination_id}             logging.view
    POST /admin/logging/{destination_id}             logging.manage
    POST /admin/logging/{destination_id}/enable      logging.manage
    POST /admin/logging/{destination_id}/delete      logging.manage
    POST /admin/logging/{destination_id}/test        logging.manage
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission
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
from app.domain.audit import Category
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.domain.log_destination_service import LogDestinationService
from app.domain.log_destinations import (
    LEVELS,
    SHIPPED_CATEGORIES,
    DestinationDraft,
    DestinationKind,
    DestinationStatus,
    LogDestination,
    Reason,
    State,
)
from app.web.errors import FormError
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

View = Annotated[AdminContext, Depends(RequirePermission(Permission.LOGGING_VIEW))]
Manage = Annotated[AdminContext, Depends(RequirePermission(Permission.LOGGING_MANAGE, fresh=True))]

KIND_LABELS: dict[DestinationKind, str] = {
    DestinationKind.SYSLOG: "admin.logging.kind.syslog",
    DestinationKind.SPLUNK_HEC: "admin.logging.kind.splunk_hec",
    DestinationKind.ELASTIC: "admin.logging.kind.elastic",
    DestinationKind.HTTP: "admin.logging.kind.http",
}
"""Label per kind; a test checks every kind has one."""

REASON_LABELS: dict[str, str] = {
    Reason.UNREACHABLE.value: "admin.logging.reason.unreachable",
    Reason.REFUSED.value: "admin.logging.reason.refused",
    Reason.TLS.value: "admin.logging.reason.tls",
    Reason.SERVER.value: "admin.logging.reason.server",
    Reason.NOT_READY.value: "admin.logging.reason.not_ready",
}
"""Explanation per reason; a test checks every reason has one."""

CATEGORY_LABELS: dict[Category, str] = {
    Category.AUDIT: "admin.logging.category.audit",
    Category.SYSTEM: "admin.logging.category.system",
    Category.SECURITY: "admin.logging.category.security",
}
"""Label per audit category a destination may take."""

LEVEL_LABELS: dict[str, str] = {
    "trace": "admin.logging.level.trace",
    "debug": "admin.logging.level.debug",
    "info": "admin.logging.level.info",
    "warning": "admin.logging.level.warning",
    "error": "admin.logging.level.error",
    "critical": "admin.logging.level.critical",
}
"""Label per minimum level."""

FIELD_NAMES = frozenset(
    {"name", "kind", "address", "index", "source", "min_level", "categories", "ca_pem", "secret"}
)
"""The inputs a domain refusal may point at."""

BASE = "/admin/logging"


def service(request: Request) -> LogDestinationService:
    """Return the application's log destination service."""
    found: LogDestinationService = request.app.state.log_destination_service
    return found


def _url(destination_id: int | None = None) -> str:
    """Return the list, or the page of one destination."""
    return f"{BASE}/{destination_id}" if destination_id is not None else BASE


@dataclass(frozen=True)
class StatusView:
    """What one process reports, translated for the page."""

    service: str
    state: State
    sent: int
    dropped: int
    failed: int
    queued: int
    last_ok: str | None
    last_error: str | None
    reason: str | None
    updated: str | None


@dataclass(frozen=True)
class DestinationView:
    """A destination as the page shows it; never a credential."""

    destination: LogDestination
    kind_label: str
    categories: list[str]
    level_label: str
    tested: str | None
    updated: str
    health: State
    """The worst state any process reports: failing, else ok, else idle."""
    status: list[StatusView]


def _status(request: Request, locale: RequestLocale, reported: DestinationStatus) -> StatusView:
    """Translate one process's status."""
    _ = locale.gettext
    return StatusView(
        reported.service,
        reported.state,
        reported.sent,
        reported.dropped,
        reported.failed,
        reported.queued,
        admin_moment(request, locale, reported.last_ok_at) if reported.last_ok_at else None,
        admin_moment(request, locale, reported.last_error_at) if reported.last_error_at else None,
        _(REASON_LABELS[reported.last_error]) if reported.last_error in REASON_LABELS else None,
        admin_moment(request, locale, reported.updated_at) if reported.updated_at else None,
    )


def _health(reported: list[DestinationStatus]) -> State:
    """Return failing if any process fails, else ok if any works, else idle."""
    states = {s.state for s in reported}
    if State.FAILING in states:
        return State.FAILING
    return State.OK if State.OK in states else State.IDLE


def _view(
    request: Request,
    locale: RequestLocale,
    destination: LogDestination,
    reported: list[DestinationStatus],
) -> DestinationView:
    """Translate a destination and its status for the list and the detail page."""
    _ = locale.gettext
    return DestinationView(
        destination,
        _(KIND_LABELS[destination.kind]),
        [_(CATEGORY_LABELS[c]) for c in SHIPPED_CATEGORIES if c in destination.categories],
        _(LEVEL_LABELS.get(destination.min_level, LEVEL_LABELS["info"])),
        admin_moment(request, locale, destination.tested_at) if destination.tested_at else None,
        admin_moment(request, locale, destination.updated_at),
        _health(reported),
        [_status(request, locale, s) for s in reported],
    )


def _fields(
    request: Request,
    locale: RequestLocale,
    form: FormData,
    current: LogDestination | None,
) -> list[Field]:
    """Return the inputs of the form, from the submission or the stored destination."""
    _ = locale.gettext

    def value(name: str, stored: str) -> str:
        return form.values.get(name, stored) if form.values else stored

    fields = [
        Field(
            "name",
            _("admin.logging.name"),
            value=value("name", current.name if current else ""),
            hint=_("admin.logging.name_hint"),
            attributes={"maxlength": "80"},
        ),
        Field(
            "kind",
            _("admin.logging.kind"),
            kind="select",
            value=value("kind", current.kind.value if current else DestinationKind.SYSLOG.value),
            hint=_("admin.logging.kind_hint"),
            options=options((k.value, _(KIND_LABELS[k])) for k in DestinationKind),
        ),
        Field(
            "address",
            _("admin.logging.address"),
            kind="url",
            value=value("address", current.address if current else ""),
            hint=_("admin.logging.address_hint"),
            attributes={"maxlength": "500", "autocomplete": "off"},
        ),
        Field(
            "secret",
            _("admin.logging.secret"),
            kind="password",
            hint=_("admin.logging.secret_hint"),
            required=False,
            attributes={"maxlength": "512", "autocomplete": "new-password"},
        ),
        Field(
            "index",
            _("admin.logging.index"),
            value=value("index", current.index if current else ""),
            hint=_("admin.logging.index_hint"),
            required=False,
            attributes={"maxlength": "120"},
        ),
        Field(
            "source",
            _("admin.logging.source"),
            value=value("source", current.source if current else ""),
            hint=_("admin.logging.source_hint"),
            required=False,
            attributes={"maxlength": "120"},
        ),
        Field(
            "min_level",
            _("admin.logging.min_level"),
            kind="select",
            value=value("min_level", current.min_level if current else "info"),
            hint=_("admin.logging.min_level_hint"),
            options=options((level, _(LEVEL_LABELS[level])) for level in LEVELS),
        ),
        Field(
            "ca_pem",
            _("admin.logging.ca_pem"),
            kind="textarea",
            value=value("ca_pem", current.ca_pem if current else ""),
            hint=_("admin.logging.ca_pem_hint"),
            required=False,
            attributes={"rows": "5", "spellcheck": "false", "autocomplete": "off"},
        ),
    ]
    filled = [
        Field(**{**f.__dict__, "error": form.error_for(f.name)}) if form.errors else f
        for f in fields
    ]
    return with_manual(request, locale, filled)


def _choices(
    locale: RequestLocale, form: FormData, current: LogDestination | None
) -> dict[str, object]:
    """Return the checkboxes: the audit categories and the two opt-ins."""
    _ = locale.gettext
    posted = bool(form.values)

    def checked(name: str, stored: bool) -> bool:
        return form.text(name) == "on" if posted else stored

    return {
        "categories": [
            {
                "name": f"category_{c.value}",
                "label": _(CATEGORY_LABELS[c]),
                "checked": checked(
                    f"category_{c.value}", c in current.categories if current else True
                ),
            }
            for c in SHIPPED_CATEGORIES
        ],
        "include_trace": checked("include_trace", current.include_trace if current else False),
        "include_diagnostic": checked(
            "include_diagnostic", current.include_diagnostic if current else False
        ),
    }


def _draft(form: FormData) -> DestinationDraft:
    """Build the draft from the form; the domain validates it.

    An unknown kind or level is passed on as an invalid value and refused there.
    """
    try:
        kind = DestinationKind(form.text("kind"))
    except ValueError:
        kind = DestinationKind.HTTP
        form.values["address"] = ""  # a draft of a kind that does not exist has no address
    return DestinationDraft(
        name=form.text("name"),
        kind=kind,
        address=form.text("address"),
        index=form.text("index"),
        source=form.text("source"),
        min_level=form.text("min_level"),
        categories=frozenset(
            c for c in SHIPPED_CATEGORIES if form.text(f"category_{c.value}") == "on"
        ),
        include_diagnostic=form.text("include_diagnostic") == "on",
        include_trace=form.text("include_trace") == "on",
        ca_pem=form.text("ca_pem"),
    )


def _secret(form: FormData) -> str | None:
    """Return the submitted credential, or ``None`` when the field was left blank."""
    return form.text("secret") or None


def _refuse(form: FormData, error: DomainError) -> None:
    """Add the refusal to the form, linked to the input it is about."""
    form.refusal(error)
    last = form.errors.pop()
    if error.field in FIELD_NAMES:
        form.errors.append(FormError(error.field, last.message))
    else:
        form.errors.append(last)


def _test_outcome(request: Request, locale: RequestLocale) -> tuple[str | None, str | None]:
    """Return the success line or the failure explanation named in the query, if any.

    Only a reason from the closed list and a three-digit code are ever shown; whatever else
    the query holds is ignored, so the query can never put text on the page.
    """
    _ = locale.gettext
    if request.query_params.get("tested") == "ok":
        return _("admin.logging.test_ok"), None
    reason = request.query_params.get("test_failed", "")
    if reason not in REASON_LABELS:
        return None, None
    raw = request.query_params.get("code", "")
    code = _("admin.logging.test_code", code=raw) if raw.isdigit() and len(raw) == 3 else ""
    return None, f"{_('admin.logging.test_failed')} {_(REASON_LABELS[reason])} {code}".strip()


async def _form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    form: FormData,
    detail: tuple[LogDestination, list[DestinationStatus]] | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render the form, alone (new) or under the destination's status (detail)."""
    current = detail[0] if detail else None
    tested, test_failed = _test_outcome(request, locale)
    response = _page(
        request,
        locale,
        "admin/logging_destination.html",
        context.principal,
        {
            "view": _view(request, locale, detail[0], detail[1]) if detail else None,
            "fields": _fields(request, locale, form, current),
            "choices": _choices(locale, form, current),
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
            "tested": tested,
            "test_failed": test_failed,
            "kinds_with_secret": [k.value for k in DestinationKind if k.has_credential],
        },
    )
    response.status_code = status
    return response


@router.get("/logging", response_class=HTMLResponse)
async def destinations(request: Request, locale: Locale, context: View) -> HTMLResponse:
    """Show every destination with its health."""
    overview = await service(request).overview(context.principal)
    return _page(
        request,
        locale,
        "admin/logging.html",
        context.principal,
        {
            "destinations": [
                _view(request, locale, d, overview.status.get(d.id or 0, []))
                for d in overview.destinations
            ],
            "done": done_message(request, locale.gettext),
        },
    )


@router.get("/logging/new", response_class=HTMLResponse)
async def new_destination(request: Request, locale: Locale, context: Manage) -> Response:
    """Show the form for a new destination."""
    return await _form_page(request, locale, context, FormData({}, locale.gettext))


@router.post("/logging", response_class=HTMLResponse)
async def create_destination(
    request: Request, locale: Locale, _: SameOrigin, context: Manage
) -> Response:
    """Add a destination, switched off and untested."""
    form = await read_form(request, locale)
    try:
        created = await service(request).create(context.principal, _draft(form), _secret(form))
    except DomainError as error:
        raise_refusal(error)
        _refuse(form, error)
        return await _form_page(request, locale, context, form, status=422)
    return redirect_done(_url(created.id), "logging_created")


@router.get("/logging/{destination_id}", response_class=HTMLResponse)
async def destination_page(
    request: Request, locale: Locale, destination_id: int, context: View
) -> Response:
    """Show one destination: status, actions and the form."""
    detail = await service(request).destination(context.principal, destination_id)
    return await _form_page(request, locale, context, FormData({}, locale.gettext), detail)


@router.post("/logging/{destination_id}", response_class=HTMLResponse)
async def update_destination(
    request: Request, locale: Locale, destination_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Change a destination."""
    detail = await service(request).destination(context.principal, destination_id)
    form = await read_form(request, locale)
    try:
        changed = await service(request).update(
            context.principal, destination_id, _draft(form), _secret(form)
        )
    except DomainError as error:
        raise_refusal(error)
        _refuse(form, error)
        return await _form_page(request, locale, context, form, detail, 422)
    return redirect_done(_url(destination_id), "saved" if changed else "unchanged")


@router.post("/logging/{destination_id}/enable", response_class=HTMLResponse)
async def enable_destination(
    request: Request, locale: Locale, destination_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Switch the destination on (``enabled=on``) or off."""
    form = await read_form(request, locale)
    enabled = form.text("enabled") == "on"
    try:
        changed = await service(request).set_enabled(context.principal, destination_id, enabled)
    except DomainError as error:
        raise_refusal(error)
        raise
    if not changed:
        return redirect_done(_url(destination_id), "unchanged")
    return redirect_done(_url(destination_id), "logging_enabled" if enabled else "logging_disabled")


@router.post("/logging/{destination_id}/delete", response_class=HTMLResponse)
async def delete_destination(
    request: Request, locale: Locale, destination_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Remove the destination with its credential and status."""
    try:
        await service(request).delete(context.principal, destination_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(_url(), "removed")


@router.post("/logging/{destination_id}/test", response_class=HTMLResponse)
async def send_test_line(
    request: Request, locale: Locale, destination_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Send one test line and show the verdict."""
    try:
        result = await service(request).send_test(context.principal, destination_id)
    except DomainError as error:
        raise_refusal(error)
        raise
    if result.ok:
        return RedirectResponse(f"{_url(destination_id)}?tested=ok", status_code=303)
    code = f"&code={result.code}" if result.code is not None else ""
    return RedirectResponse(
        f"{_url(destination_id)}?test_failed={result.reason}{code}", status_code=303
    )
