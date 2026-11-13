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
    GET  /admin/groups/{slug}/flows/{code}/subjects/{subject}
                                                         the extra skills of one offered subject
    POST /admin/groups/{slug}/flows/{code}/subjects/{subject}
                                                         save them into the draft
    POST /admin/groups/{slug}/flows/{code}/prepare       start a new revision from the published one
    POST /admin/groups/{slug}/flows/{code}/publish       the draft becomes the current revision
    GET  /admin/groups/{slug}/flows/{code}/discard       confirmation
    POST /admin/groups/{slug}/flows/{code}/discard       throw the draft away
    POST /admin/groups/{slug}/flows/{code}/activate      refused until the checklist is in order
    GET  /admin/groups/{slug}/flows/{code}/archive       confirmation
    POST /admin/groups/{slug}/flows/{code}/archive       archive an active flow
    GET  /admin/groups/{slug}/flows/{code}/delete        confirmation
    POST /admin/groups/{slug}/flows/{code}/delete        delete a flow that was never active

No script: what depends on the contact channel follows the saved value. What the lifecycle
allows is ``FlowPage.actions`` (the domain's ``flow_actions``): this module computes none of it.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, flow_service, skill_service
from app.admin.flow_inputs import (
    CHOICE_FIELD,
    FLOW_REFUSALS,
    LIMIT_NOTICE,
    SUBJECT_REFUSALS,
    SUBJECTS_FIELD,
    empty_draft,
    from_revision,
    parse_draft,
)
from app.admin.flow_views import (
    STATE_LABELS,
    CheckView,
    ConfirmKind,
    action_select,
    actions_visible,
    activation_view,
    checklist_section,
    code_field,
    completion_select,
    confirm_view,
    default_checks,
    dispatch_select,
    duration_field,
    flow_row,
    intake_checks,
    kind_views,
    name_fields,
    pool_select,
    publish_blocked,
    readout,
    required_choices,
    subject_views,
    summary_rows,
)
from app.admin.forms import FormData, read_form, redirect_done
from app.admin.group_routes import group_page
from app.admin.pages import Locale, SameOrigin, archived_requested, with_manual
from app.admin.skill_inputs import choices as skill_choices
from app.admin.skill_inputs import chosen_ids, raise_unknown, refuse, ticked_ids
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError
from app.domain.flow_readiness import (
    ReadinessItem,
)
from app.domain.flow_service import FlowChoices, FlowDraftInput, FlowPage
from app.domain.service_flows import (
    FlowState,
    ServiceFlow,
)
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
        raise_unknown(error, FLOW_REFUSALS)
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
    notice: str | None = None,
) -> HTMLResponse:
    """Render a new flow's form, the editor of a draft, or a published revision read-only.

    ``values`` are what the form shows (the submitted ones after a refusal, else the stored
    revision's); ``refused`` is the checklist of a refused activation, judged on the current
    revision; ``notice`` says that an input was changed for the admin.
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
        raise_unknown(error, FLOW_REFUSALS)
    flow = page.flow if page else None
    saved = flow.working if flow else None
    shown_values = values or (
        from_revision(saved, flow.default_for) if flow and saved else empty_draft()
    )
    editing = page is None or page.actions.save
    state = flow.state if flow else None
    fields = [code_field(form, locale, code), *name_fields(form, shown_values, locale)]
    if flow is None or saved is None:
        heading = _("admin.flows.new_heading")
    else:
        names = saved.labels
        heading = names.get(locale.code) or names.get("nl") or flow.code
    section = checklist_section(page, refused, locale)
    activation = activation_view(page, locale) if page else None
    named = with_manual(request, locale, [*fields, duration_field(form, shown_values, locale)])
    base = _url(slug, code)
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
            "in_progress": bool(flow and flow.in_progress),
            "action": _url(slug) if page is None else base,
            "name_fields": named[:-1],
            "duration_field": named[-1],
            "intake_checks": intake_checks(shown_values, locale),
            "kinds": kind_views(shown_values, locale),
            "kind_error": form.error_for("interaction_kind"),
            "readout": readout(
                saved.interaction_kind if saved else None,
                saved.handling_action if saved else None,
                locale,
            ),
            "action_select": action_select(
                saved.interaction_kind if saved else None, shown_values, form, locale
            ),
            "pool_select": pool_select(choices.pools, shown_values, form, locale),
            "skill_choices": required_choices(choices.skills, shown_values, locale),
            "skills_field": CHOICE_FIELD,
            "skills_error": form.error_for(CHOICE_FIELD),
            "subjects": subject_views(page, choices, shown_values, locale, base),
            "subjects_error": form.error_for(SUBJECTS_FIELD),
            "dispatch_select": dispatch_select(shown_values, form, locale),
            "completion_select": completion_select(shown_values, form, locale),
            "collects_email": CheckView(
                "collects_email", _("admin.flows.collects_email"), shown_values.collects_email
            ),
            "default_checks": default_checks(
                shown_values,
                saved.intake_modes if saved else None,
                bool(flow and flow.defaults_fixed),
                locale,
            ),
            "defaults_hint": _(
                "admin.flows.defaults_fixed"
                if flow and flow.defaults_fixed
                else "admin.flows.defaults_hint"
            ),
            "defaults_error": form.error_for("default_appointment"),
            "readiness": section.view if section else None,
            "readiness_basis": section.basis if section else "",
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
            "actions": page.actions if page else None,
            "activation": activation,
            "publish_blocked": bool(page and publish_blocked(page.actions)),
            "show_actions": bool(page and actions_visible(page.actions, activation)),
            "notice": notice,
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
        raise_unknown(error, FLOW_REFUSALS)
    parsed = parse_draft(form, subjects, skill_service(request).max_skills)
    code = form.text("flow_code")
    if not code:
        form.errors.append(
            FormError("flow_code", locale.gettext("admin.flows.error.code_required"))
        )
    if not form.errors:
        try:
            await flow_service(request).create(context.principal, slug, code, parsed.values)
        except DomainError as error:
            refuse(form, error, locale, CHOICE_FIELD, FLOW_REFUSALS)
        else:
            return redirect_done(_url(slug, code), "flow_created")
    return await _editor(request, locale, context, slug, None, form, parsed.values, 422)


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
        raise_unknown(error, FLOW_REFUSALS)
    parsed = parse_draft(
        form,
        page.choices.subjects,
        skill_service(request).max_skills,
        fixed=page.flow.defaults_fixed,
    )
    if not form.errors:
        try:
            await flow_service(request).save(context.principal, slug, code, parsed.values)
        except DomainError as error:
            refuse(form, error, locale, CHOICE_FIELD, FLOW_REFUSALS)
        else:
            done = "flow_saved_adjusted" if parsed.adjusted else "flow_saved"
            return redirect_done(_url(slug, code), done)
    notice = locale.gettext("admin.flows.notice.action_adjusted") if parsed.adjusted else None
    return await _editor(
        request, locale, context, slug, code, form, parsed.values, 422, notice=notice
    )


Lifecycle = Callable[[Principal, str, str], Awaitable[ServiceFlow]]
"""A use case of the flow service that changes the flow ``code`` of a service."""


async def _change(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    code: str,
    run: Lifecycle,
    done: str,
) -> Response:
    """Run the lifecycle use case ``run`` on flow ``code``; redirect or refuse."""
    form = FormData({}, locale.gettext)
    try:
        await run(context.principal, slug, code)
    except DomainError as error:
        refuse(form, error, locale, CHOICE_FIELD, FLOW_REFUSALS)
        return await _editor(request, locale, context, slug, code, form, None, 422)
    return redirect_done(_url(slug, code), done)


@router.post("/groups/{slug}/flows/{code}/prepare", response_class=HTMLResponse)
async def prepare_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Start a new revision: a draft copied from the published one."""
    return await _change(
        request, locale, context, slug, code, flow_service(request).prepare, "flow_prepared"
    )


@router.post("/groups/{slug}/flows/{code}/publish", response_class=HTMLResponse)
async def publish_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Publish the draft; the flow is not activated by it."""
    return await _change(
        request, locale, context, slug, code, flow_service(request).publish, "flow_published"
    )


async def _confirm_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    code: str,
    kind: ConfirmKind,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render what discarding the draft, archiving or deleting the flow does, and ask to confirm."""
    try:
        page = await flow_service(request).page(context.principal, slug, code)
    except DomainError as error:
        raise_unknown(error, FLOW_REFUSALS)
    view = confirm_view(kind, page.flow, page.actions, locale)
    return group_page(
        request,
        locale,
        context,
        "admin/group_flow_confirm.html",
        {
            "what": kind.value,
            "code": code,
            "name": view.name,
            "heading": view.heading,
            "lead": view.lead,
            "effects": view.effects,
            "button": view.button,
            "allowed": view.allowed,
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
        request, locale, context, slug, code, ConfirmKind.DISCARD, FormData({}, locale.gettext)
    )


async def _confirmed(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    code: str,
    kind: ConfirmKind,
    run: Callable[[Principal, str, str], Awaitable[object]],
    done: str,
    after: str,
) -> Response:
    """Run the change ``kind`` confirmed on flow ``code``; redirect to ``after`` or refuse."""
    form = FormData({}, locale.gettext)
    try:
        await run(context.principal, slug, code)
    except DomainError as error:
        refuse(form, error, locale, CHOICE_FIELD, FLOW_REFUSALS)
        return await _confirm_page(request, locale, context, slug, code, kind, form, 422)
    return redirect_done(after, done)


@router.post("/groups/{slug}/flows/{code}/discard", response_class=HTMLResponse)
async def discard_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Throw the draft away; the published revision stays."""
    return await _confirmed(
        request,
        locale,
        context,
        slug,
        code,
        ConfirmKind.DISCARD,
        flow_service(request).discard,
        "flow_discarded",
        _url(slug, code),
    )


@router.post("/groups/{slug}/flows/{code}/activate", response_class=HTMLResponse)
async def activate_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Ask to activate a flow; refused, with the checklist, until every point is in order."""
    form = FormData({}, locale.gettext)
    try:
        outcome = await flow_service(request).activate(context.principal, slug, code)
    except DomainError as error:
        refuse(form, error, locale, CHOICE_FIELD, FLOW_REFUSALS)
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
        request, locale, context, slug, code, ConfirmKind.ARCHIVE, FormData({}, locale.gettext)
    )


@router.post("/groups/{slug}/flows/{code}/archive", response_class=HTMLResponse)
async def archive_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Archive an active flow: no new request goes through it; the ones it has keep it."""
    return await _confirmed(
        request,
        locale,
        context,
        slug,
        code,
        ConfirmKind.ARCHIVE,
        flow_service(request).archive,
        "flow_archived",
        _url(slug),
    )


@router.get("/groups/{slug}/flows/{code}/delete", response_class=HTMLResponse)
async def delete_form(
    request: Request, locale: Locale, slug: str, code: str, context: Manage
) -> Response:
    """Show what deleting a flow that was never active does and ask for the confirmation."""
    return await _confirm_page(
        request, locale, context, slug, code, ConfirmKind.DELETE, FormData({}, locale.gettext)
    )


@router.post("/groups/{slug}/flows/{code}/delete", response_class=HTMLResponse)
async def delete_flow(
    request: Request, locale: Locale, slug: str, code: str, _: SameOrigin, context: Manage
) -> Response:
    """Delete a flow that was never active, with all its revisions."""
    return await _confirmed(
        request,
        locale,
        context,
        slug,
        code,
        ConfirmKind.DELETE,
        flow_service(request).delete,
        "flow_deleted",
        _url(slug),
    )


async def _subject_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    slug: str,
    code: str,
    subject: str,
    form: FormData,
    status: int = 200,
) -> HTMLResponse:
    """Render the page that chooses the extra skills of one offered subject of the draft."""
    _ = locale.gettext
    try:
        page = await flow_service(request).subject_page(context.principal, slug, code, subject)
    except DomainError as error:
        raise_unknown(error, SUBJECT_REFUSALS)
    chosen = ticked_ids(form) if form.values else page.chosen
    return group_page(
        request,
        locale,
        context,
        "admin/group_flow_subject.html",
        {
            "code": code,
            "heading": _("admin.flows.extra_skills_for", subject=page.subject.label(locale.code)),
            "subject_code": subject,
            "name": page.flow.working.labels.get(locale.code) or page.flow.code,
            "subject": page.subject.label(locale.code),
            "skill_choices": skill_choices(page.skills, chosen, locale.code),
            "skills_field": CHOICE_FIELD,
            "skills_error": form.error_for(CHOICE_FIELD),
            "back": f"{_url(slug, code)}#{SUBJECTS_FIELD}",
            "errors": form.errors,
        },
        status,
    )


@router.get("/groups/{slug}/flows/{code}/subjects/{subject}", response_class=HTMLResponse)
async def subject_form(
    request: Request, locale: Locale, slug: str, code: str, subject: str, context: Manage
) -> Response:
    """Show the extra skills one offered subject of the flow's draft requires."""
    return await _subject_page(
        request, locale, context, slug, code, subject, FormData({}, locale.gettext)
    )


@router.post("/groups/{slug}/flows/{code}/subjects/{subject}", response_class=HTMLResponse)
async def save_subject(
    request: Request,
    locale: Locale,
    slug: str,
    code: str,
    subject: str,
    _: SameOrigin,
    context: Manage,
) -> Response:
    """Save the extra skills of one offered subject into the flow's draft."""
    form = await read_form(request, locale)
    try:
        ids = chosen_ids(form, skill_service(request).max_skills)
    except DomainError:
        form.errors.append(FormError(CHOICE_FIELD, locale.gettext("admin.flows.error.choice")))
    else:
        try:
            await flow_service(request).save_subject_skills(
                context.principal, slug, code, subject, ids
            )
        except DomainError as error:
            refuse(form, error, locale, CHOICE_FIELD, SUBJECT_REFUSALS)
        else:
            return redirect_done(_url(slug, code), "flow_subject_saved", anchor=SUBJECTS_FIELD)
    return await _subject_page(request, locale, context, slug, code, subject, form, 422)
