"""Admin routes for the flows of a service (ADR 0121 step 2d).

The flows live under the service's *Settings* tab as the sub-tab *Flows* (ADR 0047: service →
Settings → Flows). Every route depends on exactly one ``RequirePermission(group.edit)`` for the
service in its path; the changes and the pages that hold a form need a recent login, are POST
forms with the Fetch Metadata check, and are answered with a redirect and a confirmation.
``FlowService`` authorises again, validates and audits. A flow is addressed by its code, never
its id; a flow of another service, an unknown code and a service's existing (legacy) flow answer
404, so a code tells nothing about what exists elsewhere.

    GET  /admin/groups/{slug}/flows                      the flows, ``?flows=archived`` for those
    GET  /admin/groups/{slug}/flows/new                  the form of a new flow
    POST /admin/groups/{slug}/flows                      create it as a draft
    GET  /admin/groups/{slug}/flows/{code}               the editor of the draft, or the published
                                                         revision with a button to prepare a new one
    POST /admin/groups/{slug}/flows/{code}               save the draft
    POST /admin/groups/{slug}/flows/{code}/prepare       start a new revision from the published one
    POST /admin/groups/{slug}/flows/{code}/publish       the draft becomes the current revision
    GET  /admin/groups/{slug}/flows/{code}/discard       confirmation
    POST /admin/groups/{slug}/flows/{code}/discard       delete the draft
    POST /admin/groups/{slug}/flows/{code}/activate      refused until the checklist is in order
    GET  /admin/groups/{slug}/flows/{code}/archive       confirmation
    POST /admin/groups/{slug}/flows/{code}/archive       archive an active flow

No script: what depends on the contact channel follows the saved value.
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, flow_service, skill_service
from app.admin.flow_inputs import (
    CHOICE_FIELD,
    LIMIT_NOTICE,
    empty_draft,
    from_revision,
    parse_draft,
    raise_unknown,
    refuse,
)
from app.admin.flow_views import (
    STATE_LABELS,
    CheckView,
    action_select,
    code_field,
    completion_select,
    default_checks,
    dispatch_select,
    duration_field,
    flow_row,
    intake_checks,
    kind_views,
    name_fields,
    pool_select,
    readiness_view,
    readout,
    required_choices,
    subject_views,
    summary_rows,
)
from app.admin.forms import FormData, read_form, redirect_done
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin, archived_requested, with_manual
from app.domain.authorization import Permission
from app.domain.errors import DomainError
from app.domain.flow_service import FlowChoices, FlowDraftInput, FlowPage
from app.domain.service_flows import FlowState, ReadinessItem
from app.web.errors import FormError
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

View = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.GROUP_EDIT, group_param="slug")),
]
"""The permission to read the list of a service's flows."""

Manage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.GROUP_EDIT, group_param="slug", fresh=True)),
]
"""The permission of every form and every change, as for the rest of the group's settings."""

FLOWS_FILTER = "flows"
"""``?flows=archived`` lists the archived flows."""


def _url(slug: str, code: str | None = None) -> str:
    """Return the list of the service's flows, or the page of its flow ``code``."""
    base = f"/admin/groups/{slug}/flows"
    return base if code is None else f"{base}/{code}"


async def _list_page(
    request: Request, locale: RequestLocale, context: AdminContext, slug: str
) -> HTMLResponse:
    """Render the list of the service's flows."""
    archived = archived_requested(request, FLOWS_FILTER)
    try:
        found = await flow_service(request).overview(context.principal, slug, archived=archived)
    except DomainError as error:
        raise_unknown(error)
    return group_page(
        request,
        locale,
        context,
        "admin/group_flows.html",
        {
            "rows": [flow_row(line, locale) for line in found.lines],
            "archived": archived,
            "can_add": found.can_add,
            "limit": found.limit,
            "limit_notice": LIMIT_NOTICE,
            "errors": [],
        },
    )


def _basis(page: FlowPage, locale: RequestLocale, refused: bool) -> str:
    """Return which revision the checklist judges, in words."""
    _ = locale.gettext
    if refused or page.flow.draft is None:
        number = page.flow.current.revision if page.flow.current else page.shown.revision
        return _("admin.flows.basis_published", revision=number)
    return _("admin.flows.basis_draft", revision=page.shown.revision)


async def _editor(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    code: str | None,
    form: FormData,
    values: FlowDraftInput | None = None,
    status: int = 200,
    refused: Sequence[ReadinessItem] | None = None,
) -> HTMLResponse:
    """Render a new flow's form, the editor of a draft, or a published revision read-only.

    ``values`` are what the form shows (the submitted ones after a refusal, else the stored
    revision's); ``refused`` is the checklist of a refused activation, judged on the current
    revision.
    """
    _ = locale.gettext
    service = flow_service(request)
    page: FlowPage | None = None
    try:
        if code is None:
            choices: FlowChoices = (await service.form(context.principal, slug)).choices
        else:
            page = await service.page(context.principal, slug, code)
            choices = page.choices
    except DomainError as error:
        raise_unknown(error)
    shown_values = values or (
        from_revision(page.shown, page.flow.default_for) if page else empty_draft()
    )
    flow = page.flow if page else None
    fixed = flow is not None and flow.current is not None
    editing = page is None or page.editable
    saved = page.shown if page else None
    state = flow.state if flow else None
    items = refused if refused is not None else (page.items if page else None)
    can_activate = page.can_activate if page else False
    activate_hint = _(
        "admin.flows.activate_publish_first"
        if flow and flow.current is None
        else "admin.flows.activate_ready"
        if can_activate
        else "admin.flows.activate_not_ready"
    )
    modes = saved.intake_modes if saved else None
    fields = [
        code_field(form, locale, code),
        *name_fields(form, shown_values, locale),
    ]
    if page is None:
        heading = _("admin.flows.new_heading")
    else:
        names = page.shown.labels
        heading = names.get(locale.code) or names.get("nl") or page.flow.code
    named = with_manual(request, locale, [*fields, duration_field(form, shown_values, locale)])
    return group_page(
        request,
        locale,
        context,
        "admin/group_flow.html",
        {
            "new": page is None,
            "code": code or "",
            "heading": heading,
            "editing": editing,
            "archived": state is FlowState.ARCHIVED,
            "state_word": _(STATE_LABELS[state]) if state else "",
            "in_progress": bool(flow and flow.draft is not None and flow.current is not None),
            "action": _url(slug) if page is None else _url(slug, code),
            "name_fields": named[:-1],
            "duration_field": named[-1],
            "intake_checks": intake_checks(shown_values, locale),
            "kinds": kind_views(shown_values, locale),
            "kind_error": form.error_for("interaction_kind"),
            "readout": readout(saved.interaction_kind if saved else None, locale),
            "action_select": action_select(
                saved.interaction_kind if saved else None, shown_values, form, locale
            ),
            "pool_select": pool_select(choices.pools, shown_values, form, locale),
            "skill_choices": required_choices(choices.skills, shown_values, locale),
            "skills_field": CHOICE_FIELD,
            "skills_error": form.error_for(CHOICE_FIELD),
            "subjects": subject_views(choices, shown_values, locale),
            "subjects_error": form.error_for("subjects"),
            "dispatch_select": dispatch_select(shown_values, form, locale),
            "completion_select": completion_select(shown_values, form, locale),
            "collects_email": CheckView(
                "collects_email", _("admin.flows.collects_email"), shown_values.collects_email
            ),
            "default_checks": default_checks(shown_values, modes, fixed, locale),
            "defaults_hint": _(
                "admin.flows.defaults_fixed" if fixed else "admin.flows.defaults_hint"
            ),
            "defaults_error": form.error_for("default_appointment"),
            "readiness": readiness_view(items, locale) if items is not None else None,
            "readiness_basis": _basis(page, locale, refused is not None) if page else "",
            "summary_rows": (
                summary_rows(
                    page.flow.current,
                    page.flow.default_for,
                    page.pool,
                    page.skills,
                    {s.code: s.label(locale.code) for s in choices.subjects},
                    locale,
                )
                if page and not editing and page.flow.current
                else []
            ),
            "revision_number": page.flow.current.revision if page and page.flow.current else 0,
            "can_prepare": bool(
                page
                and not editing
                and flow
                and flow.current
                and flow.state is not FlowState.ARCHIVED
            ),
            "can_publish": bool(page and editing and state is not FlowState.ARCHIVED),
            "can_discard": bool(flow and flow.draft and state is not FlowState.ARCHIVED),
            "can_archive": state is FlowState.ACTIVE,
            "show_activate": state is FlowState.DRAFT,
            "can_activate": can_activate,
            "activate_hint": activate_hint,
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/flows", response_class=HTMLResponse)
async def flows(request: Request, locale: Locale, slug: str, context: View) -> Response:
    """Show the flows of one service; the archived ones with ``?flows=archived``."""
    return await _list_page(request, locale, context, slug)


@router.get("/groups/{slug}/flows/new", response_class=HTMLResponse)
async def new_flow_form(request: Request, locale: Locale, slug: str, context: Manage) -> Response:
    """Show the form of a new flow."""
    return await _editor(request, locale, context, slug, None, FormData({}, locale.gettext))


@router.post("/groups/{slug}/flows", response_class=HTMLResponse)
async def create_flow(
    request: Request, locale: Locale, slug: str, _: SameOrigin, context: Manage
) -> Response:
    """Create a flow as a draft: its code (fixed from then on), its names and what is chosen."""
    form = await read_form(request, locale)
    try:
        subjects = (await flow_service(request).form(context.principal, slug)).choices.subjects
    except DomainError as error:
        raise_unknown(error)
    values = parse_draft(form, subjects, skill_service(request).max_skills)
    code = form.text("flow_code")
    if not form.errors:
        try:
            await flow_service(request).create(context.principal, slug, code, values)
        except DomainError as error:
            refuse(form, error, locale)
        else:
            return redirect_done(_url(slug, code), "flow_created")
    return await _editor(request, locale, context, slug, None, form, values, 422)


@router.get("/groups/{slug}/flows/{code}", response_class=HTMLResponse)
async def flow_page(
    request: Request, locale: Locale, slug: str, code: str, context: Manage
) -> Response:
    """Show the editor of a flow's draft, or its published revision read-only."""
    return await _editor(request, locale, context, slug, code, FormData({}, locale.gettext))


@router.post("/groups/{slug}/flows/{code}", response_class=HTMLResponse)
async def save_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Save the draft revision of a flow."""
    form = await read_form(request, locale)
    try:
        page = await flow_service(request).page(context.principal, slug, code)
    except DomainError as error:
        raise_unknown(error)
    values = parse_draft(
        form,
        page.choices.subjects,
        skill_service(request).max_skills,
        fixed=page.flow.current is not None,
    )
    if not form.errors:
        try:
            await flow_service(request).save(context.principal, slug, code, values)
        except DomainError as error:
            refuse(form, error, locale)
        else:
            return redirect_done(_url(slug, code), "flow_saved")
    return await _editor(request, locale, context, slug, code, form, values, 422)


async def _change(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    code: str,
    action: str,
    done: str,
) -> Response:
    """Run the lifecycle action ``action`` of the service on flow ``code``; redirect or refuse."""
    form = FormData({}, locale.gettext)
    service = flow_service(request)
    run = {
        "prepare": service.prepare,
        "publish": service.publish,
    }[action]
    try:
        await run(context.principal, slug, code)
    except DomainError as error:
        refuse(form, error, locale)
        return await _editor(request, locale, context, slug, code, form, None, 422)
    return redirect_done(_url(slug, code), done)


@router.post("/groups/{slug}/flows/{code}/prepare", response_class=HTMLResponse)
async def prepare_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Start a new revision: a draft copied from the published one."""
    return await _change(request, locale, context, slug, code, "prepare", "flow_prepared")


@router.post("/groups/{slug}/flows/{code}/publish", response_class=HTMLResponse)
async def publish_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Publish the draft; the flow is not activated by it."""
    return await _change(request, locale, context, slug, code, "publish", "flow_published")


async def _confirm_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    code: str,
    what: str,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render what discarding the draft or archiving the flow does, and ask for confirmation."""
    _ = locale.gettext
    try:
        page = await flow_service(request).page(context.principal, slug, code)
    except DomainError as error:
        raise_unknown(error)
    flow = page.flow
    name = page.shown.labels.get(locale.code) or page.shown.labels.get("nl") or flow.code
    if what == "discard":
        allowed = flow.draft is not None and flow.state is not FlowState.ARCHIVED
        heading = _("admin.flows.discard_title")
        button = _("admin.flows.discard_button")
        lead = _("admin.flows.discard_lead" if allowed else "admin.flows.discard_none")
        effects = (
            [_("admin.flows.discard_effect_draft")]
            + [
                _(
                    "admin.flows.discard_effect_published"
                    if flow.current
                    else "admin.flows.discard_effect_gone"
                )
            ]
            if allowed
            else []
        )
    else:
        allowed = flow.state is FlowState.ACTIVE
        heading = _("admin.flows.archive_title")
        button = _("admin.flows.archive_button")
        lead = _("admin.flows.archive_lead" if allowed else "admin.flows.archive_not_active")
        effects = (
            [
                _("admin.flows.archive_effect_new"),
                _("admin.flows.archive_effect_keeps"),
                _("admin.flows.archive_effect_listed"),
            ]
            if allowed
            else []
        )
    return group_page(
        request,
        locale,
        context,
        "admin/group_flow_confirm.html",
        {
            "what": what,
            "code": code,
            "name": name,
            "heading": heading,
            "lead": lead,
            "effects": effects,
            "button": button,
            "allowed": allowed,
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/flows/{code}/discard", response_class=HTMLResponse)
async def discard_form(
    request: Request, locale: Locale, slug: str, code: str, context: Manage
) -> Response:
    """Show what discarding the draft does and ask for the confirmation."""
    return await _confirm_page(
        request, locale, context, slug, code, "discard", FormData({}, locale.gettext)
    )


@router.post("/groups/{slug}/flows/{code}/discard", response_class=HTMLResponse)
async def discard_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Delete the draft; a flow that was never published disappears with it."""
    form = FormData({}, locale.gettext)
    try:
        gone = await flow_service(request).discard(context.principal, slug, code)
    except DomainError as error:
        refuse(form, error, locale)
        return await _confirm_page(request, locale, context, slug, code, "discard", form, 422)
    if gone:
        return redirect_done(_url(slug), "flow_discarded_gone")
    return redirect_done(_url(slug, code), "flow_discarded")


@router.post("/groups/{slug}/flows/{code}/activate", response_class=HTMLResponse)
async def activate_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Ask to activate a flow; refused, with the checklist, until every point is in order."""
    form = FormData({}, locale.gettext)
    try:
        outcome = await flow_service(request).activate(context.principal, slug, code)
    except DomainError as error:
        refuse(form, error, locale)
        return await _editor(request, locale, context, slug, code, form, None, 422)
    if outcome.activated:
        return redirect_done(_url(slug, code), "flow_activated")
    form.errors.append(FormError(None, form._("admin.flows.error.not_ready")))
    return await _editor(request, locale, context, slug, code, form, None, 409, outcome.items)


@router.get("/groups/{slug}/flows/{code}/archive", response_class=HTMLResponse)
async def archive_form(
    request: Request, locale: Locale, slug: str, code: str, context: Manage
) -> Response:
    """Show what archiving a flow does and ask for the confirmation."""
    return await _confirm_page(
        request, locale, context, slug, code, "archive", FormData({}, locale.gettext)
    )


@router.post("/groups/{slug}/flows/{code}/archive", response_class=HTMLResponse)
async def archive_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Archive an active flow: no new request goes through it; the ones it has keep it."""
    form = FormData({}, locale.gettext)
    try:
        await flow_service(request).archive(context.principal, slug, code)
    except DomainError as error:
        refuse(form, error, locale)
        return await _confirm_page(request, locale, context, slug, code, "archive", form, 422)
    return redirect_done(_url(slug), "flow_archived")
