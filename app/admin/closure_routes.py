"""Admin routes for emergency closures (ADR 0024).

Every route depends on exactly one ``RequirePermission``, always ``fresh``: closing a team is
a change with impact on citizens. Changes are POST forms with the Fetch Metadata check,
answered with a redirect and a confirmation (post/redirect/get). ``ClosureService``
authorises again, validates and audits.

    GET  /admin/groups/{slug}/closure                         closure.manage   status and forms
    POST /admin/groups/{slug}/closure                         closure.manage   close the group
    POST /admin/groups/{slug}/closure/update                  closure.manage   end, texts, contacts
    POST /admin/groups/{slug}/closure/lift                    closure.manage   open again
    POST /admin/groups/{slug}/closure/reinstate               closure.manage   reinstate all
    POST /admin/groups/{slug}/closure/cancel                  closure.manage   cancel all
    POST /admin/groups/{slug}/closure/bookings/{id}/reinstate closure.manage   one booking
    POST /admin/groups/{slug}/closure/bookings/{id}/cancel    closure.manage   one booking
    GET  /admin/groups/{slug}/closure/bookings/{id}/move      closure.manage   choose a slot
    POST /admin/groups/{slug}/closure/bookings/{id}/move      closure.manage   move it
    GET  /admin/closures                                      organisation.closure  organisations
    GET  /admin/organisations/{org}/closure                   organisation.closure  all groups
    POST /admin/organisations/{org}/closure                   organisation.closure  close all
    POST /admin/organisations/{org}/closure/lift              organisation.closure  lift all

The closing and update forms also take an optional heading per locale; the closure copies the
contact block that applies to the group (ADR 0037 §2), edited on its own page
(``contact_routes``) and shown here as a preview with a count. The image of a running closure
(ADR 0037 §3) has its own form on this page, posted to ``closure_image_routes``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import (
    Field,
    FormData,
    done_message,
    raise_refusal,
    read_form,
    redirect_done,
    slot_options,
)
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin, page, with_manual
from app.domain.authorization import Permission
from app.domain.booking import Booking
from app.domain.closure import (
    MAX_HEADING_LENGTH,
    MAX_NOTICE_LENGTH,
    NOTICE_LOCALES,
    Closure,
    heading_field,
    notice_field,
)
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.errors import DomainError
from app.domain.images import ALT_LOCALES, IMAGE_TYPES, MAX_ALT_LENGTH, MAX_IMAGE_BYTES, alt_field
from app.domain.ports import Clock
from app.domain.scheduling import Group
from app.domain.services import BookingService
from app.i18n.dates import local_day
from app.web.closure_labels import ClosureView, closure_view, moment_label
from app.web.contact_labels import contact_service
from app.web.errors import status_for
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest
from app.web.slot_values import parse_start

router = APIRouter(prefix="/admin", include_in_schema=False)

MOVE_DAYS = 28
"""How far ahead the move form offers free slots."""

NOTICE_LABELS: dict[str, tuple[str, str]] = {
    "nl": ("admin.closure.notice_nl", "admin.closure.notice_hint"),
    "en": ("admin.closure.notice_en", "admin.closure.notice_hint"),
}
"""Label and hint key of the notice per required locale (literal for the catalogue test)."""

HEADING_LABELS: dict[str, str] = {
    "nl": "admin.closure.heading_nl",
    "en": "admin.closure.heading_en",
}
"""Label key of the optional heading per locale (literal for the catalogue test)."""

ManageClosure = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.CLOSURE_MANAGE, group_param="slug", fresh=True)),
]
OrganisationClosure = Annotated[
    AdminContext,
    Depends(
        RequirePermission(Permission.ORGANISATION_CLOSURE, organisation_param="org", fresh=True)
    ),
]


def closure_service(request: Request) -> ClosureService:
    """Return the application's closure service."""
    service: ClosureService = request.app.state.closure_service
    return service


def _booking_service(request: Request) -> BookingService:
    """Return the application's booking service (for free slots)."""
    service: BookingService = request.app.state.booking_service
    return service


def _clock(request: Request) -> Clock:
    """Return the application's clock."""
    clock: Clock = request.app.state.clock
    return clock


def _url(slug: str) -> str:
    """Return the closure page of group ``slug``."""
    return f"/admin/groups/{slug}/closure"


def _organisation_url(org: str) -> str:
    """Return the closure page of organisation ``org``."""
    return f"/admin/organisations/{org}/closure"


@dataclass(frozen=True)
class SuspendedView:
    """A booking on hold as the admin sees it: reference and original time, no contact data."""

    id: int
    reference: str
    when: str
    purged: bool


def _suspended(bookings: Sequence[Booking], group: Group, locale: str) -> list[SuspendedView]:
    """Return the bookings on hold for display."""
    return [
        SuspendedView(
            b.id or 0, b.reference, moment_label(b.start, group, locale), b.contact is None
        )
        for b in bookings
    ]


# --- forms ----------------------------------------------------------------------------------


def _local_input(value: datetime | None) -> str:
    """Return a local datetime as the value of a ``datetime`` field (no seconds)."""
    return value.strftime("%Y-%m-%dT%H:%M") if value else ""


def _closure_fields(
    form: FormData,
    locale: RequestLocale,
    group: Group,
    current: Closure | None,
) -> list[Field]:
    """Return the inputs of the closing form, or of the update form for ``current``."""
    _ = locale.gettext
    fields: list[Field] = []
    if current is None:
        fields.append(
            Field(
                "starts_at",
                _("admin.closure.starts_at"),
                "datetime",
                form.moment_value("starts_at"),
                hint=_("admin.closure.starts_at_hint"),
                error=form.error_for("starts_at"),
                required=False,
            )
        )
    ends = current.ends_at.astimezone(group.tz) if current and current.ends_at else None
    fields.append(
        Field(
            "ends_at",
            _("admin.closure.ends_at"),
            "datetime",
            form.moment_value("ends_at", _local_input(ends)),
            hint=_("admin.closure.ends_at_hint"),
            error=form.error_for("ends_at"),
            required=False,
        )
    )
    for code in NOTICE_LOCALES:
        name = notice_field(code)
        label_key, hint_key = NOTICE_LABELS[code]
        fields.append(
            Field(
                name,
                _(label_key),
                "textarea",
                form.values.get(name, current.notice(code) if current else ""),
                hint=_(hint_key, limit=MAX_NOTICE_LENGTH),
                error=form.error_for(name),
                attributes={"maxlength": str(MAX_NOTICE_LENGTH)},
            )
        )
    for code in NOTICE_LOCALES:
        name = heading_field(code)
        fields.append(
            Field(
                name,
                _(HEADING_LABELS[code]),
                value=form.values.get(name, current.headings.get(code, "") if current else ""),
                hint=_("admin.closure.heading_hint"),
                error=form.error_for(name),
                required=False,
                attributes={"maxlength": str(MAX_HEADING_LENGTH)},
            )
        )
    return fields


ALT_LABELS: dict[str, str] = {
    "nl": "admin.closure.image_alt_nl",
    "en": "admin.closure.image_alt_en",
}
"""Label key of the image's text alternative per locale (literal for the catalogue test)."""


def _image_fields(form: FormData, locale: RequestLocale, current: Closure | None) -> list[Field]:
    """Return the inputs of the image form: the file and the text alternative per locale."""
    _ = locale.gettext
    image = current.image if current else None
    fields = [
        Field(
            "image",
            _("admin.closure.image_file"),
            "file",
            hint=_("admin.closure.image_hint", kb=MAX_IMAGE_BYTES // 1000),
            error=form.error_for("image"),
            required=image is None,
            attributes={"accept": ",".join(IMAGE_TYPES)},
        )
    ]
    for code in ALT_LOCALES:
        name = alt_field(code)
        stored = image.text.alts.get(code, "") if image else ""
        fields.append(
            Field(
                name,
                _(ALT_LABELS[code]),
                value=form.values.get(name, stored),
                hint=_("admin.closure.image_alt_hint"),
                error=form.error_for(name),
                required=False,
                attributes={"maxlength": str(MAX_ALT_LENGTH)},
            )
        )
    return fields


def _decorative(form: FormData, current: Closure | None) -> bool:
    """Return whether the "decorative" box is ticked: as submitted, else as stored."""
    if form.values:
        return form.checked("decorative")
    return bool(current and current.image and current.image.text.decorative)


def _notices(form: FormData) -> dict[str, str]:
    """Return the submitted notice per required locale."""
    return {code: form.values.get(notice_field(code), "") for code in NOTICE_LOCALES}


def _headings(form: FormData) -> dict[str, str]:
    """Return the submitted heading per locale; empty means the default heading."""
    return {code: form.text(heading_field(code)) for code in NOTICE_LOCALES}


def _closure_input(form: FormData, group: Group) -> ClosureInput:
    """Read the closing form; parse errors are collected on ``form``."""
    return ClosureInput(
        form.optional_moment("starts_at", group.tz),
        form.optional_moment("ends_at", group.tz),
        _notices(form),
        _headings(form),
    )


# --- one group ------------------------------------------------------------------------------


async def closure_page_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the closure page of the group."""
    group = context.group.group
    overview = await closure_service(request).overview(context.principal, group.slug)
    now = _clock(request).now()
    banner: ClosureView | None = (
        closure_view(overview.closure, group, locale, now) if overview.closure else None
    )
    contacts = await contact_service(request).view_group(context.principal, group.slug)
    applies = contacts.block or contacts.organisation_default
    return group_page(
        request,
        locale,
        context,
        "admin/group_closure.html",
        {
            "closure": banner,
            "fields": _closure_fields(form, locale, group, overview.closure),
            "suspended": _suspended(overview.suspended, group, locale.code),
            "confirmed_ahead": overview.confirmed_ahead,
            "closure_contacts": banner.contacts if banner else None,
            "image_fields": with_manual(
                request, locale, _image_fields(form, locale, overview.closure)
            ),
            "image_decorative": _decorative(form, overview.closure),
            "own_contacts": contacts.block is not None,
            "contact_count": len(applies.options) if applies else 0,
            "errors": form.errors,
        },
        status,
    )


async def _refused(
    request: Request, locale: RequestLocale, context: AdminContext, error: DomainError
) -> HTMLResponse:
    """Render the closure page with the refusal of an action in the error summary."""
    raise_refusal(error)
    form = FormData({}, locale.gettext)
    form.refusal(error)
    return await closure_page_response(request, locale, context, form, status_for(error))


@router.get("/groups/{slug}/closure", response_class=HTMLResponse)
async def closure_page(
    request: Request, locale: Locale, slug: str, context: ManageClosure
) -> HTMLResponse:
    """Show the group's closure, the bookings on hold and the forms."""
    return await closure_page_response(request, locale, context, FormData({}, locale.gettext))


@router.post("/groups/{slug}/closure", response_class=HTMLResponse)
async def start_closure(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageClosure
) -> Response:
    """Close the group; the bookings inside the closure are put on hold."""
    form = await read_form(request, locale)
    closure = _closure_input(form, context.group.group)
    if not form.errors:
        try:
            count = await closure_service(request).close_group(context.principal, slug, closure)
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return redirect_done(_url(slug), "closure_started", count)
    return await closure_page_response(request, locale, context, form, 422)


@router.post("/groups/{slug}/closure/update", response_class=HTMLResponse)
async def update_closure(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageClosure
) -> Response:
    """Change the end and notices; optionally mail the update to everyone on hold."""
    form = await read_form(request, locale)
    ends_at = form.optional_moment("ends_at", context.group.group.tz)
    if not form.errors:
        try:
            outcome = await closure_service(request).update(
                context.principal,
                slug,
                ends_at,
                _notices(form),
                notify=form.checked("notify"),
                headings=_headings(form),
                refresh_contacts=form.checked("refresh_contacts"),
            )
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return redirect_done(_url(slug), "closure_updated", outcome.notified)
    return await closure_page_response(request, locale, context, form, 422)


@router.post("/groups/{slug}/closure/lift", response_class=HTMLResponse)
async def lift_closure(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageClosure
) -> Response:
    """Open the group again; optionally reinstate what can still go ahead."""
    form = await read_form(request, locale)
    try:
        outcome = await closure_service(request).lift(
            context.principal, slug, reinstate=form.checked("reinstate")
        )
    except DomainError as error:
        return await _refused(request, locale, context, error)
    return redirect_done(_url(slug), "closure_lifted", outcome.done)


@router.post("/groups/{slug}/closure/reinstate", response_class=HTMLResponse)
async def reinstate_all(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageClosure
) -> Response:
    """Reinstate every booking on hold that can still go ahead."""
    try:
        outcome = await closure_service(request).reinstate_all(context.principal, slug)
    except DomainError as error:
        return await _refused(request, locale, context, error)
    return redirect_done(_url(slug), "reinstated", outcome.done)


@router.post("/groups/{slug}/closure/cancel", response_class=HTMLResponse)
async def cancel_all(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: ManageClosure
) -> Response:
    """Cancel every booking on hold; each citizen gets the cancellation mail."""
    try:
        outcome = await closure_service(request).cancel_all(context.principal, slug)
    except DomainError as error:
        return await _refused(request, locale, context, error)
    return redirect_done(_url(slug), "cancelled", outcome.done)


@router.post("/groups/{slug}/closure/bookings/{booking_id}/reinstate", response_class=HTMLResponse)
async def reinstate_one(
    request: Request,
    locale: Locale,
    slug: str,
    booking_id: int,
    _: SameOrigin,
    context: ManageClosure,
) -> Response:
    """Reinstate one booking on hold."""
    try:
        await closure_service(request).reinstate(context.principal, slug, booking_id)
    except DomainError as error:
        return await _refused(request, locale, context, error)
    return redirect_done(_url(slug), "reinstated", 1)


@router.post("/groups/{slug}/closure/bookings/{booking_id}/cancel", response_class=HTMLResponse)
async def cancel_one(
    request: Request,
    locale: Locale,
    slug: str,
    booking_id: int,
    _: SameOrigin,
    context: ManageClosure,
) -> Response:
    """Cancel one booking on hold."""
    try:
        await closure_service(request).cancel(context.principal, slug, booking_id)
    except DomainError as error:
        return await _refused(request, locale, context, error)
    return redirect_done(_url(slug), "cancelled", 1)


async def _move_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    booking_id: int,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the form that moves one booking on hold to a free slot."""
    group = context.group.group
    booking = await closure_service(request).booking(context.principal, group.slug, booking_id)
    today = local_day(_clock(request).now(), group.tz)
    free = await _booking_service(request).availability(
        group.slug, today, today + timedelta(days=MOVE_DAYS)
    )
    _ = locale.gettext
    options = slot_options([slot.start for slot in free], group.tz, locale.code)
    field = Field(
        "start",
        _("admin.closure.move_start"),
        "select",
        form.text("start"),
        hint=_("admin.closure.move_hint", days=MOVE_DAYS),
        error=form.error_for("start"),
        options=options,
    )
    return group_page(
        request,
        locale,
        context,
        "admin/closure_move.html",
        {
            "booking": _suspended([booking], group, locale.code)[0],
            "field": field,
            "has_slots": bool(options),
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/closure/bookings/{booking_id}/move", response_class=HTMLResponse)
async def move_form(
    request: Request, locale: Locale, slug: str, booking_id: int, context: ManageClosure
) -> HTMLResponse:
    """Show the free slots one booking on hold can move to."""
    return await _move_response(request, locale, context, booking_id, FormData({}, locale.gettext))


@router.post("/groups/{slug}/closure/bookings/{booking_id}/move", response_class=HTMLResponse)
async def move_one(
    request: Request,
    locale: Locale,
    slug: str,
    booking_id: int,
    _: SameOrigin,
    context: ManageClosure,
) -> Response:
    """Move one booking on hold; the citizen gets the change mail with a new link."""
    form = await read_form(request, locale)
    start = parse_start(form.text("start"))
    if start is None:
        raise RejectedRequest(400)
    try:
        await closure_service(request).move(context.principal, slug, booking_id, start)
    except DomainError as error:
        raise_refusal(error)
        form.refusal(error)
        return await _move_response(request, locale, context, booking_id, form, status_for(error))
    return redirect_done(_url(slug), "moved", 1)


# --- a whole organisation ---------------------------------------------------------------------


@dataclass(frozen=True)
class GroupStateView:
    """One group on the organisation's closure page."""

    name: str
    slug: str
    closure: ClosureView | None
    suspended: int


@router.get("/closures", response_class=HTMLResponse)
async def organisations(
    request: Request,
    locale: Locale,
    context: Annotated[
        AdminContext, Depends(RequirePermission(Permission.ORGANISATION_CLOSURE, fresh=True))
    ],
) -> HTMLResponse:
    """List the organisations the admin may close as a whole."""
    found = await closure_service(request).organisations(context.principal)
    return page(
        request, locale, "admin/closure_list.html", context.principal, {"organisations": found}
    )


async def _organisation_response(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the organisation's closure page."""
    states = await closure_service(request).organisation_overview(context.principal, org)
    now = _clock(request).now()
    groups = [
        GroupStateView(
            s.summary.group.name,
            s.summary.group.slug,
            closure_view(s.closure, s.summary.group, locale, now) if s.closure else None,
            s.suspended,
        )
        for s in states
    ]
    name = states[0].summary.organisation_name if states else org
    fields = _closure_fields(form, locale, states[0].summary.group, None) if states else []
    contacts = await contact_service(request).view(context.principal, org)
    response = page(
        request,
        locale,
        "admin/organisation_closure.html",
        context.principal,
        {
            "organisation_slug": org,
            "organisation_name": name,
            "groups": groups,
            "any_closed": any(g.closure for g in groups),
            "contact_count": len(contacts.block.options) if contacts.block else 0,
            "fields": fields,
            "errors": form.errors,
            "done": done_message(request, locale.gettext),
        },
    )
    response.status_code = status
    return response


@router.get("/organisations/{org}/closure", response_class=HTMLResponse)
async def organisation_page(
    request: Request, locale: Locale, org: str, context: OrganisationClosure
) -> HTMLResponse:
    """Show every group of the organisation with its closure, and the close-all form."""
    return await _organisation_response(request, locale, context, org, FormData({}, locale.gettext))


@router.post("/organisations/{org}/closure", response_class=HTMLResponse)
async def close_organisation(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: OrganisationClosure
) -> Response:
    """Close every group of the organisation that is not closed yet."""
    form = await read_form(request, locale)
    states = await closure_service(request).organisation_overview(context.principal, org)
    if not states:
        raise RejectedRequest(400)
    # Times are entered in the organisation's first group's zone (groups share one in practice).
    closure = _closure_input(form, states[0].summary.group)
    if not form.errors:
        try:
            outcomes = await closure_service(request).close_organisation(
                context.principal, org, closure
            )
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            closed = sum(1 for o in outcomes if o.changed)
            return redirect_done(_organisation_url(org), "organisation_closed", closed)
    return await _organisation_response(request, locale, context, org, form, 422)


@router.post("/organisations/{org}/closure/lift", response_class=HTMLResponse)
async def lift_organisation(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: OrganisationClosure
) -> Response:
    """Lift the closure of every group of the organisation."""
    form = await read_form(request, locale)
    outcomes = await closure_service(request).lift_organisation(
        context.principal, org, reinstate=form.checked("reinstate")
    )
    lifted = sum(1 for o in outcomes if o.changed)
    return redirect_done(_organisation_url(org), "organisation_lifted", lifted)
