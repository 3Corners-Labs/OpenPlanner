"""System status and Advanced settings (ADR 0042 §11, build steps 3 and 4).

One page for ``settings.platform.view`` (superuser and auditor):

- *System status*: every class A and B variable with whether it is set and where from, never
  its value; the latest check of the public address's certificate (ADR 0064); for a
  development or test installation, what production would refuse, in the words of the
  start-up check;
- *Data fields* (ADR 0063 §6): the fixed list of personal-data fields of a booking with
  its classification, read-only, with a link to the manual topic ``data-fields``;
- every class C and D setting by topic, with its effective value, its source and a lock note
  naming the variable when the environment locks it. Secrets show only whether they are set.

With ``settings.platform.manage`` (superuser, recent login) the settings that build step 4
covers get a form: a new value within the portal bounds, or *Use the default* when the portal
holds one. ``LOG_LEVEL`` also takes the hours after which it falls back (at most 24). A
refusal is shown at the row, linked to its field; the submitted text is never echoed into
the audit log. The page shows one form per topic with one *Save*: it saves every value of the
topic that differs from the current one (an empty secret keeps the current one) and stops at
the first refusal; *Use the default* and *Manage this here* post to their own route.

The page reloads the portal values first, so it always shows what the processes now apply.

The certificate block (ADR 0070) also carries the source of the bundled proxy's certificate
and the own certificate: the source is the setting ``tls_certificate_source``, changed through
the generic route but checked by ``CertificateSourceService`` (``own`` needs a chain whose key
is on this host); a key is made here with its signing request (downloaded as text), the chain
the CA issued is uploaded (with its key only to import an existing certificate), replaced or
removed there, and *Check now* runs the worker's probe at once. The private key never passes
through a response. Without the bundled proxy the block says the source does not apply.

    GET  /admin/settings                          settings.platform.view
    POST /admin/settings/topic/{topic}            settings.platform.manage (recent login)
    POST /admin/settings/{key}                    settings.platform.manage (recent login)
    POST /admin/settings/{key}/reset              settings.platform.manage (recent login)
    POST /admin/settings/{key}/adopt              settings.platform.manage (recent login)
    POST /admin/settings/login/test               settings.login.manage (recent login): dry run (§8)
    POST /admin/settings/tls/csr                  settings.platform.manage (recent login): new key
    GET  /admin/settings/tls/csr.pem              settings.platform.manage: the pending request
    POST /admin/settings/tls/certificate          settings.platform.manage (recent login): upload
    POST /admin/settings/tls/certificate/remove   settings.platform.manage (recent login)
    POST /admin/settings/tls/check                settings.platform.manage (recent login)
    POST /admin/settings/package/export           settings.platform.manage (recent login): a file
    POST /admin/settings/package/import           settings.platform.manage (recent login): upload

The block *Configuration package* (ADR 0089) exports every configuration table with the
secrets sealed under a passphrase, and imports such a package into an installation that is
not in use. The passphrase is read from the form and never echoed, logged or stored.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from app.adapters.live_settings import LiveSettings
from app.adapters.settings_report import SettingState
from app.admin.authz import AdminContext, RequirePermission, can_function
from app.admin.forms import CODE_MESSAGES, done_message, raise_refusal, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin, admin_moment
from app.admin.pages import page as _page
from app.admin.session import save_login_trial
from app.admin.uploads import read_uploads
from app.config import Settings
from app.domain.authorization import Permission
from app.domain.configuration_package import (
    MEDIA_TYPE,
    MIN_PASSPHRASE_LENGTH,
    package_filename,
)
from app.domain.configuration_service import ConfigurationPackageService
from app.domain.errors import DomainError, ErrorCode
from app.domain.login_mapping import LOGIN_KEYS, validate_mapping
from app.domain.own_certificate import (
    MAX_PEM_BYTES,
    SOURCE_KEY,
    SOURCES,
    OwnCertificate,
    SourceStatus,
)
from app.domain.privacy import DATA_FIELDS, ENTITY, Classification
from app.domain.settings_registry import (
    BY_KEY,
    LOG_LEVEL_MAX_EXPIRY_HOURS,
    Kind,
    SettingClass,
    Topic,
)
from app.domain.settings_service import LOG_LEVEL, SettingsService, editable
from app.domain.tls import Finding
from app.domain.tls_check import CertificateStatus
from app.domain.tls_source import CertificateSourceService
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest
from app.web.settings_labels import (
    CLASS_LABELS,
    PROBLEM_LABELS,
    SETTING_LABELS,
    SOURCE_LABELS,
    TLS_FINDING_LABELS,
    TOPIC_LABELS,
)

router = APIRouter(prefix="/admin", include_in_schema=False)

URL = "/admin/settings"
View = Annotated[AdminContext, Depends(RequirePermission(Permission.SETTINGS_PLATFORM_VIEW))]
Manage = Annotated[AdminContext, Depends(RequirePermission(Permission.SETTINGS_PLATFORM_MANAGE))]
Change = Annotated[
    AdminContext, Depends(RequirePermission(Permission.SETTINGS_PLATFORM_MANAGE, fresh=True))
]

_ENVIRONMENT_ONLY = (SettingClass.BOOTSTRAP, SettingClass.DEPLOYMENT)


@dataclass(frozen=True)
class StatusRow:
    """One class A or B variable: whether it is set and where from, never its value."""

    variable: str
    class_label: str
    source: str
    is_set: bool


@dataclass(frozen=True)
class SettingRow:
    """One class C or D setting as the table shows it."""

    label: str
    variable: str
    value: str
    source: str
    locked: bool
    outside_bounds: bool
    portal_ignored: bool
    key: str = ""
    editable: bool = False
    input: str = ""
    choices: tuple[str, ...] = ()
    from_portal: bool = False
    expires: bool = False
    error: str | None = None
    secret: bool = False
    adoptable: bool = False
    adopted: bool = False
    boolean: bool = False


@dataclass(frozen=True)
class CertificateView:
    """The latest certificate check as System status shows it (ADR 0064)."""

    host: str
    finding: str
    chip: str
    checked_at: str
    issuer: str = ""
    valid_until: str = ""


_DANGER = (Finding.EXPIRED, Finding.NOT_YET_VALID)

TLS_URL = f"{URL}#tls-heading"
CERTIFICATE_FIELDS = ("chain", "key")
REMOVE_KEY = "tls_certificate"
"""The error key of the remove form (the upload's errors sit at ``chain`` or ``key``)."""
PACKAGE_KEY = "package"
"""The error key of the configuration package block (ADR 0089)."""
PACKAGE_FIELDS = ("passphrase", "confirm")
"""The text fields next to the uploaded package."""
CSR_KEY = "tls_csr"
"""The error key of the request form."""


@dataclass(frozen=True)
class UploadedView:
    """The own certificate uploaded in the portal, as the source form shows it (ADR 0070)."""

    fingerprint: str
    subject: str
    issuer: str
    names: str
    valid_until: str
    uploaded_at: str


@dataclass(frozen=True)
class SourceView:
    """The certificate source block: the setting, the key on this host, the chain, the forms.

    Attributes:
        applicable: Whether the bundled proxy runs; otherwise only a note is shown.
        source: The effective source (``acme`` or ``own``).
        choices: The sources to choose from.
        locked: Whether the environment locks the source (the host manages it).
        variable: The locking variable's name.
        editable: Whether the forms are shown (manage permission, not locked).
        uploaded: The stored chain, if any.
        key_missing: A chain is stored but its key is not on this host (restored elsewhere).
        pending_csr: A signing request waiting for its certificate, if any.
        removable: Whether the chain may be removed (not in use).
        errors: Refusals by field: the source, the chain, the key or the removal.
    """

    applicable: bool
    source: str
    choices: tuple[str, ...]
    locked: bool
    variable: str
    editable: bool
    uploaded: UploadedView | None
    key_missing: bool
    pending_csr: str | None
    removable: bool
    errors: dict[str, str]


@dataclass(frozen=True)
class TopicSection:
    """The settings of one topic."""

    key: str
    heading: str
    rows: list[SettingRow]


@dataclass(frozen=True)
class Problem:
    """What production would refuse, translated."""

    variable: str
    text: str


@dataclass(frozen=True)
class DataFieldRow:
    """One personal-data field on the read-only *Data fields* card (ADR 0063 §6).

    Attributes:
        name: The field as it is called on the booking and in the database.
        label: Its name for the reader.
        classification: *direct* or *sensitive*, as a chip label.
        tone: The chip's tone (``danger`` for direct, ``warning`` for sensitive).
        entity: What it is stored on; always the booking.
    """

    name: str
    label: str
    classification: str
    tone: str
    entity: str


_FIELD_LABELS = {
    "first_name": "admin.settings.data_fields.field.first_name",
    "last_name": "admin.settings.data_fields.field.last_name",
    "email": "admin.settings.data_fields.field.email",
    "phone": "admin.settings.data_fields.field.phone",
    "note": "admin.settings.data_fields.field.note",
    "form_answers": "admin.settings.data_fields.field.form_answers",
    "guest_join_url": "admin.settings.data_fields.field.guest_join_url",
    "host_join_url": "admin.settings.data_fields.field.host_join_url",
    "trial_join_url": "admin.settings.data_fields.field.trial_join_url",
}
"""Label per personal-data field; a test fails when the list and this map drift apart."""
_CLASS_LABELS = {
    Classification.DIRECT: ("admin.settings.data_fields.direct", "danger"),
    Classification.SENSITIVE: ("admin.settings.data_fields.sensitive", "warning"),
}


def data_field_rows(locale: RequestLocale) -> list[DataFieldRow]:
    """Return the fixed personal-data list of ``app.domain.privacy`` for the card, translated."""
    _ = locale.gettext
    return [
        DataFieldRow(
            f.name,
            _(_FIELD_LABELS[f.name]),
            _(_CLASS_LABELS[f.classification][0]),
            _CLASS_LABELS[f.classification][1],
            ENTITY,
        )
        for f in DATA_FIELDS
    ]


@router.get("/settings", response_class=HTMLResponse)
async def advanced_settings(request: Request, locale: Locale, context: View) -> HTMLResponse:
    """Show System status and every portal setting with its value and source."""
    return await _overview(request, locale, context)


LoginChange = Annotated[
    AdminContext, Depends(RequirePermission(Permission.SETTINGS_LOGIN_MANAGE, fresh=True))
]


@router.post("/settings/login/test", response_class=HTMLResponse)
async def try_login_mapping(
    request: Request, locale: Locale, _: SameOrigin, context: LoginChange
) -> Response:
    """Check a proposed login mapping, then dry-run it on a fresh login (ADR 0042 §8).

    Nothing is saved here: the proposal waits in the signed session until the next login,
    and is saved only if that login still resolves to this superuser.
    """
    form = await read_form(request, locale)
    try:
        values = validate_mapping({key: form.text(key) for key in LOGIN_KEYS})
    except DomainError as error:
        return await _overview(request, locale, context, (error.field or "", error), 422)
    save_login_trial(request, context.principal.subject, values, request.app.state.clock.now())
    return RedirectResponse("/admin/login?next=/admin/settings&fresh=1", status_code=303)


@router.post("/settings/topic/{topic}", response_class=HTMLResponse)
async def change_topic(
    request: Request, topic: str, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Save every changed setting of one topic; the first refusal stops and shows at its row."""
    form = await read_form(request, locale)
    live: LiveSettings = request.app.state.live_settings
    await live.refresh()
    changed = 0
    for state in live.states():
        spec = state.spec
        if spec.topic.value != topic or spec.key not in form.values or spec.key == SOURCE_KEY:
            continue
        raw = form.text(spec.key)
        if (spec.secret and not raw) or (not spec.secret and raw == _input(state)):
            continue
        hours = _hours(form.text("hours")) if spec.key == LOG_LEVEL else None
        try:
            await _service(request).change(context.principal, spec.key, raw, hours=hours)
        except DomainError as error:
            raise_refusal(error)
            return await _overview(request, locale, context, (spec.key, error), 422)
        changed += 1
    anchor = f"topic-{topic}" if topic in {t.value for t in Topic} else ""
    return redirect_done(URL, "saved" if changed else "unchanged", anchor=anchor)


@router.post("/settings/{key}", response_class=HTMLResponse)
async def change_setting(
    request: Request, key: str, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Change one setting within its portal bounds; the processes apply it within 10 s."""
    form = await read_form(request, locale)
    spec = BY_KEY.get(key)
    if spec is not None and spec.secret and not form.text("value").strip():
        return redirect_done(URL, "unchanged", anchor=_anchor(key))  # write-only: empty keeps it
    hours = _hours(form.text("hours"))
    try:
        if key == SOURCE_KEY:  # ADR 0070: `own` needs an upload, and the proxy is told at once
            await (await _source(request)).choose(context.principal, form.text("value"))
        else:
            await _service(request).change(context.principal, key, form.text("value"), hours=hours)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, (key, error), 422)
    return redirect_done(URL, "saved", anchor=_anchor(key))


@router.post("/settings/{key}/reset", response_class=HTMLResponse)
async def reset_setting(
    request: Request, key: str, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Remove the portal value, so the default (or the environment) applies again."""
    try:
        await _service(request).reset(context.principal, key)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, (key, error), 409)
    if key == SOURCE_KEY:
        await (await _source(request)).sync(SOURCES[0])  # back to the default: ACME
    return redirect_done(URL, "saved", anchor=_anchor(key))


@router.post("/settings/tls/csr", response_class=HTMLResponse)
async def request_certificate(
    request: Request, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Make a new key on this host and its signing request (ADR 0070)."""
    try:
        await (await _source(request)).generate_csr(context.principal)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, (CSR_KEY, error), 422)
    return redirect_done(URL, "csr_generated", anchor="tls-heading")


@router.get("/settings/tls/csr.pem", response_class=PlainTextResponse)
async def download_request(request: Request, context: Manage) -> Response:
    """Return the pending signing request as a file to hand to the CA (public data)."""
    status = await (await _source(request)).status()
    if status.pending_csr is None:
        raise RejectedRequest(404)
    return PlainTextResponse(
        status.pending_csr,
        media_type="application/pkcs10",
        headers={"content-disposition": 'attachment; filename="openplanner.csr"'},
    )


@router.post("/settings/tls/certificate", response_class=HTMLResponse)
async def upload_certificate(
    request: Request, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Store an uploaded chain; with a key, import an existing certificate (ADR 0070)."""
    uploads = await read_uploads(request, CERTIFICATE_FIELDS, MAX_PEM_BYTES)
    try:
        await (await _source(request)).upload(
            context.principal, uploads.files["chain"], uploads.files["key"] or None
        )
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, (error.field or "chain", error), 422)
    return redirect_done(URL, "certificate_uploaded", anchor="tls-heading")


@router.post("/settings/tls/certificate/remove", response_class=HTMLResponse)
async def remove_certificate(
    request: Request, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Remove the own certificate; refused while the source is ``own`` (ADR 0070)."""
    try:
        removed = await (await _source(request)).remove(context.principal)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, (REMOVE_KEY, error), 409)
    return redirect_done(
        URL, "certificate_removed" if removed else "unchanged", anchor="tls-heading"
    )


@router.post("/settings/tls/check", response_class=HTMLResponse)
async def check_certificate(request: Request, _: SameOrigin, context: Change) -> Response:
    """Probe the public address now and store the outcome (ADR 0070, *Check now*)."""
    try:
        await (await _source(request)).check_now(context.principal)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(URL, "certificate_checked", anchor="tls-heading")


@router.post("/settings/{key}/adopt", response_class=HTMLResponse)
async def adopt_setting(
    request: Request, key: str, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Take a value the environment locks over into the portal (ADR 0042 §12)."""
    try:
        await _service(request).adopt(context.principal, key)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, (key, error), 409)
    return redirect_done(URL, "adopted", anchor=_anchor(key))


@router.post("/settings/package/export")
async def export_package(
    request: Request, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Return a configuration package sealed under the submitted passphrase (ADR 0089)."""
    form = await read_form(request, locale)
    passphrase = form.text("passphrase")
    try:
        if passphrase != form.text("passphrase_repeat"):
            raise DomainError(ErrorCode.CONFIRMATION_MISMATCH, PACKAGE_KEY)
        package = await _packages(request).export(context.principal.subject, passphrase)
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, (PACKAGE_KEY, error), 422)
    current: Settings = request.app.state.settings
    name = package_filename(current.installation_name, request.app.state.clock.now())
    return Response(
        package,
        media_type=MEDIA_TYPE,
        headers={"content-disposition": f'attachment; filename="{name}"'},
    )


@router.post("/settings/package/import", response_class=HTMLResponse)
async def import_package(
    request: Request, locale: Locale, _: SameOrigin, context: Change
) -> Response:
    """Replace the configuration with an uploaded package (ADR 0089 point 5)."""
    current: Settings = request.app.state.settings
    uploads = await read_uploads(
        request, (PACKAGE_KEY,), current.configuration_package_max_mb * 1024 * 1024, PACKAGE_FIELDS
    )
    try:
        if uploads.values.get("confirm") != "yes":
            raise DomainError(ErrorCode.CONFIRMATION_MISMATCH, PACKAGE_KEY)
        summary = await _packages(request).import_(
            context.principal.subject,
            uploads.files[PACKAGE_KEY],
            uploads.values.get("passphrase", "").strip(),
        )
    except DomainError as error:
        raise_refusal(error)
        return await _overview(request, locale, context, (PACKAGE_KEY, error), 422)
    return redirect_done(
        URL, "package_imported", summary.counts.get("organisations", 0), anchor="package-heading"
    )


def _packages(request: Request) -> ConfigurationPackageService:
    """Return the application's configuration package service."""
    service: ConfigurationPackageService = request.app.state.configuration_packages
    return service


def _hours(raw: str) -> int | None:
    """Return the submitted expiry in hours, or ``None`` when it is not a small whole number."""
    return int(raw) if raw.isdigit() and len(raw) <= 3 else None


def _service(request: Request) -> SettingsService:
    """Return the application's settings service."""
    service: SettingsService = request.app.state.settings_service
    return service


async def _source(request: Request) -> CertificateSourceService:
    """Return the certificate source service (ADR 0070), with the portal values reloaded first.

    A change in the portal applies to the processes within ten seconds; the service's rules
    (``own`` in use, the lock) must see it at once, so the reload happens here.
    """
    live: LiveSettings = request.app.state.live_settings
    await live.refresh()
    service: CertificateSourceService = request.app.state.certificate_source
    return service


def _anchor(key: str) -> str:
    """Return the row anchor of a registered key (anything else scrolls nowhere)."""
    return f"setting-{key}" if key in BY_KEY else ""


async def _overview(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    refusal: tuple[str, DomainError] | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render the page, with a refusal shown at its row."""
    _ = locale.gettext
    live: LiveSettings = request.app.state.live_settings
    await live.refresh()
    states = live.states()
    current: Settings = request.app.state.settings
    status_rows = [
        StatusRow(s.spec.env, _(CLASS_LABELS[s.spec.class_]), _(SOURCE_LABELS[s.source]), s.is_set)
        for s in states
        if s.spec.class_ in _ENVIRONMENT_ONLY
    ]
    problems = (
        []
        if current.environment == "prod"
        else [Problem(p.variable, _(PROBLEM_LABELS[p.code])) for p in current.production_problems()]
    )
    manage = can_function(context.principal)(Permission.SETTINGS_PLATFORM_MANAGE)
    errors = {}
    if refusal is not None:
        key, error = refusal
        errors[key] = _(CODE_MESSAGES.get(error.code, "error.invalid_request"))
    response = _page(
        request,
        locale,
        "admin/settings.html",
        context.principal,
        {
            "environment": current.environment,
            "certificate": await _certificate(request, locale),
            "source": await _source_view(request, locale, states, manage=manage, errors=errors),
            "refused": live.refused,
            "status": status_rows,
            "problems": problems,
            "data_fields": data_field_rows(locale),
            "sections": _sections(
                states, locale, manage=manage, errors=errors, stored=_stored(live)
            ),
            "done": done_message(request, locale.gettext),
            "max_hours": LOG_LEVEL_MAX_EXPIRY_HOURS,
            "trace_max_hours": current.trace_level_max_hours,
            "login_form": _login_form(context, current, live, errors, locale),
            "package": {
                "error": errors.get(PACKAGE_KEY),
                "min_length": MIN_PASSPHRASE_LENGTH,
                "max_mb": current.configuration_package_max_mb,
            },
            "manage": manage,
            "unknown_error": next(
                (m for k, m in errors.items() if k not in BY_KEY and k != PACKAGE_KEY), None
            ),
        },
    )
    response.status_code = status
    return response


async def _certificate(request: Request, locale: RequestLocale) -> CertificateView | None:
    """Return the latest certificate check for the page, if the worker or the CLI ran one."""
    status: CertificateStatus = request.app.state.certificate_status
    check = await status.latest()
    if check is None:
        return None
    _ = locale.gettext
    valid_until = issuer = ""
    if check.certificate is not None:
        days = check.certificate.days_left(request.app.state.clock.now())
        valid_until = (
            f"{admin_moment(request, locale, check.certificate.not_after)} "
            f"({_('admin.settings.tls.days_left', days=days)})"
        )
        issuer = check.certificate.issuer
    chip = "success" if check.ok else "danger" if check.finding in _DANGER else "warning"
    return CertificateView(
        check.host,
        _(TLS_FINDING_LABELS[check.finding]),
        chip,
        admin_moment(request, locale, check.checked_at),
        issuer,
        valid_until,
    )


async def _source_view(
    request: Request,
    locale: RequestLocale,
    states: list[SettingState],
    *,
    manage: bool,
    errors: dict[str, str],
) -> SourceView:
    """Describe the certificate source block (ADR 0070) from the setting's state."""
    service = await _source(request)
    state = next(s for s in states if s.spec.key == SOURCE_KEY)
    status = await service.status() if service.applicable() else SourceStatus(None, False, None)
    uploaded = status.uploaded
    keys = (SOURCE_KEY, REMOVE_KEY, CSR_KEY, *CERTIFICATE_FIELDS)
    return SourceView(
        service.applicable(),
        str(state.value),
        state.spec.choices,
        state.locked,
        state.spec.env,
        manage and not state.locked,
        None if uploaded is None else _uploaded_view(request, locale, uploaded),
        status.key_missing,
        status.pending_csr,
        uploaded is not None and state.value != SOURCES[1],
        {k: v for k, v in errors.items() if k in keys},
    )


def _uploaded_view(
    request: Request, locale: RequestLocale, uploaded: OwnCertificate
) -> UploadedView:
    """Write the uploaded certificate's public data for the page."""
    days = uploaded.certificate.days_left(request.app.state.clock.now())
    return UploadedView(
        uploaded.fingerprint_text(),
        uploaded.certificate.subject,
        uploaded.certificate.issuer,
        ", ".join(uploaded.certificate.names),
        f"{admin_moment(request, locale, uploaded.certificate.not_after)} "
        f"({locale.gettext('admin.settings.tls.days_left', days=days)})",
        admin_moment(request, locale, uploaded.uploaded_at),
    )


def _sections(
    states: list[SettingState],
    locale: RequestLocale,
    *,
    manage: bool = False,
    errors: dict[str, str] | None = None,
    stored: frozenset[str] = frozenset(),
) -> list[TopicSection]:
    """Group the class C and D settings by topic, in the registry's topic order.

    ``stored`` names the keys the portal holds a value for, so a locked one that was taken
    over says which variable to remove.
    """
    _ = locale.gettext
    errors = errors or {}
    by_topic: dict[Topic, list[SettingRow]] = {}
    for state in states:
        if not state.spec.in_portal or state.spec.key == SOURCE_KEY:  # its own block (ADR 0070)
            continue
        by_topic.setdefault(state.spec.topic, []).append(
            SettingRow(
                _(SETTING_LABELS[state.spec.key]),
                state.spec.env,
                _value(state, locale),
                _(SOURCE_LABELS[state.source]),
                state.locked,
                state.outside_bounds,
                state.portal_ignored,
                key=state.spec.key,
                editable=manage and editable(state.spec) and not state.locked,
                input=_input(state),
                choices=state.spec.choices,
                from_portal=state.source.value == "portal",
                expires=state.spec.key == LOG_LEVEL,
                error=errors.get(state.spec.key),
                secret=state.spec.secret,
                adoptable=manage
                and editable(state.spec)
                and state.locked
                and state.spec.key not in stored,
                adopted=state.locked and state.spec.key in stored,
                boolean=state.spec.kind is Kind.BOOL,
            )
        )
    return [
        TopicSection(topic.value, _(TOPIC_LABELS[topic]), by_topic[topic])
        for topic in Topic
        if topic in by_topic
    ]


def _value(state: SettingState, locale: RequestLocale) -> str:
    """Write a value for the page: secrets as set or not set, booleans and empties in words."""
    _ = locale.gettext
    if state.spec.secret:
        return _("admin.settings.value.set") if state.is_set else _("admin.settings.value.not_set")
    if state.value is None:
        return _("admin.settings.value.empty")
    if isinstance(state.value, bool):
        return _("admin.settings.value.on") if state.value else _("admin.settings.value.off")
    return str(state.value)


def _input(state: SettingState) -> str:
    """Return the value as the form's input shows it (empty for a secret or no value)."""
    if state.spec.secret or state.value is None:
        return ""
    if isinstance(state.value, bool):
        return "true" if state.value else "false"
    return str(state.value)


def _stored(live: LiveSettings) -> frozenset[str]:
    """Return the keys the portal holds a value for (secrets included, values never)."""
    portal = live.portal
    return frozenset(portal.values) | frozenset(portal.secrets)


@dataclass(frozen=True)
class LoginField:
    """One field of the login mapping form."""

    key: str
    label: str
    value: str
    error: str | None


def _login_form(
    context: AdminContext,
    current: Settings,
    live: LiveSettings,
    errors: dict[str, str],
    locale: RequestLocale,
) -> list[LoginField] | None:
    """Return the login mapping form, or ``None`` without the permission or with a lock.

    Only the superuser holds ``settings.login.manage``; any key the environment locks makes
    the whole mapping the operator's (it is saved as one).
    """
    can = can_function(context.principal)(Permission.SETTINGS_LOGIN_MANAGE)
    if not can or set(LOGIN_KEYS) & live.locked():
        return None
    values = current.login_values
    return [
        LoginField(key, locale.gettext(SETTING_LABELS[key]), values[key], errors.get(key))
        for key in LOGIN_KEYS
    ]
