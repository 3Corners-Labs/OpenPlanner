"""The role editor (ADR 0039 step 3): custom roles within fixed bounds, superuser only.

Every route depends on exactly one ``RequirePermission(roles.manage, fresh=True)``. A custom
role starts as a copy of a template (organisation admin, group admin or auditor) and keeps
that template's reach. The form offers only the permissions the template may delegate;
locked and out-of-reach ones are listed with the reason, without a checkbox. ``RoleService``
authorises again, validates the bounds (``validate_role``) and audits every change. Changes
are POST forms with the Fetch Metadata check; the version in each form refuses a stale save.

    GET  /admin/roles                       custom roles and "copy a template"
    GET  /admin/roles/new?template=…        form prefilled with the template's permissions
    POST /admin/roles                       create → the role's page
    GET  /admin/roles/{role_id}             form with the stored definition
    POST /admin/roles/{role_id}             change names, claim value and permissions
    POST /admin/roles/{role_id}/delete      delete; holders keep no role from it
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, role_service
from app.admin.forms import FormData, done_message, raise_refusal, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin
from app.admin.pages import page as _page
from app.domain.authorization import (
    HELD_BACK,
    LOCKED_PERMISSIONS,
    MATRIX,
    PII_PERMISSIONS,
    REQUIRES,
    TEMPLATES,
    CustomRole,
    Permission,
    Role,
    RoleRule,
    delegable,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.roles import CLAIM_VALUE_PATTERN, MAX_NAME_LENGTH, RoleDraft, RoleInvalidError
from app.web.authz_labels import PERMISSION_LABELS, ROLE_LABELS
from app.web.authz_report import matrix_rows
from app.web.errors import FormError
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

Manage = Annotated[AdminContext, Depends(RequirePermission(Permission.ROLES_MANAGE, fresh=True))]
"""The permission of every page and change of the role editor."""

PERMISSION_FIELD = "perm."

RULE_MESSAGES: dict[RoleRule, str] = {
    RoleRule.TEMPLATE: "admin.roles.error.template",
    RoleRule.LOCKED: "admin.roles.error.locked",
    RoleRule.ABOVE_REACH: "admin.roles.error.above_reach",
    RoleRule.PII: "admin.roles.error.pii",
    RoleRule.WRITE: "admin.roles.error.write",
    RoleRule.REQUIREMENT: "admin.roles.error.requirement",
    RoleRule.BASICS: "admin.roles.error.basics",
}
"""Message per broken bound; ``{permission}`` is the permission's label."""


@dataclass(frozen=True)
class PermissionChoice:
    """One permission in the editor: a checkbox when delegable, else the reason it is not."""

    name: str
    label: str
    code: str
    available: bool
    checked: bool
    locked: bool
    pii: bool
    requires: str


@dataclass(frozen=True)
class RoleRow:
    """A custom role in the overview."""

    role: CustomRole
    name: str
    template: str


def _template(raw: str) -> Role:
    """Return the template named in a query or form; organisation admin when unknown."""
    return next((role for role in TEMPLATES if role.value == raw), Role.ORG_ADMIN)


def _choices(
    locale: RequestLocale, template: Role, form: FormData, held: frozenset[Permission]
) -> list[PermissionChoice]:
    """Return every permission in matrix order, ticked from the form or from ``held``."""
    _ = locale.gettext
    offered = delegable(template)
    return [
        PermissionChoice(
            name=f"{PERMISSION_FIELD}{row.permission}",
            label=_(PERMISSION_LABELS[row.permission]),
            code=row.permission.value,
            available=row.permission in offered,
            checked=(f"{PERMISSION_FIELD}{row.permission}" in form.values)
            if form.values
            else row.permission in held,
            locked=row.permission in LOCKED_PERMISSIONS | HELD_BACK,
            pii=row.permission in PII_PERMISSIONS,
            requires=", ".join(
                _(PERMISSION_LABELS[p]) for p in sorted(REQUIRES.get(row.permission, ()))
            ),
        )
        for row in matrix_rows()
    ]


def _violations(locale: RequestLocale, form: FormData, error: RoleInvalidError) -> None:
    """Add one message per broken bound, linked to its checkbox where there is one."""
    _ = locale.gettext
    for violation in error.violations:
        permission = violation.permission
        label = _(PERMISSION_LABELS[permission]) if permission else ""
        target = "permissions"
        if permission is not None and violation.rule in (RoleRule.REQUIREMENT, RoleRule.BASICS):
            target = f"{PERMISSION_FIELD}{permission}"
        form.errors.append(FormError(target, _(RULE_MESSAGES[violation.rule], permission=label)))


def _draft(form: FormData, template: Role) -> RoleDraft:
    """Build the draft from the submitted form (the domain validates it)."""
    return RoleDraft.create(
        template=template.value,
        permissions=[
            key[len(PERMISSION_FIELD) :] for key in form.values if key.startswith(PERMISSION_FIELD)
        ],
        claim_value=form.text("claim_value"),
        name_nl=form.text("name_nl"),
        name_en=form.text("name_en"),
    )


def _refused(locale: RequestLocale, form: FormData, error: DomainError) -> None:
    """Turn a refusal into form messages (or a 403 for ``not_permitted``)."""
    raise_refusal(error)
    if isinstance(error, RoleInvalidError):
        _violations(locale, form, error)
    elif error.code is ErrorCode.CLAIM_VALUE_TAKEN:
        form.errors.append(
            FormError("claim_value", locale.gettext("admin.form.error.claim_value_taken"))
        )
    else:
        form.refusal(error)


def _form_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    form: FormData,
    template: Role,
    current: CustomRole | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render the editor for a new role (copy of ``template``) or for ``current``."""
    _ = locale.gettext
    held = current.permissions if current else MATRIX[template]
    values = form.values

    def value(name: str, stored: str) -> str:
        return values.get(name, stored if current else "")

    response = _page(
        request,
        locale,
        "admin/role.html",
        context.principal,
        {
            "role": current,
            "template": template.value,
            "template_label": _(ROLE_LABELS[template]),
            "read_only": template is Role.AUDITOR,
            "name_nl": value("name_nl", current.name_nl if current else ""),
            "name_en": value("name_en", current.name_en if current else ""),
            "claim_value": value("claim_value", current.claim_value if current else ""),
            "claim_pattern": CLAIM_VALUE_PATTERN,
            "max_name": MAX_NAME_LENGTH,
            "choices": _choices(locale, template, form, held),
            "errors": form.errors,
            "error_for": form.error_for,
            "done": done_message(request, _),
        },
    )
    response.status_code = status
    return response


def _url(role_id: int | None = None) -> str:
    """Return the overview, or the page of one role."""
    return f"/admin/roles/{role_id}" if role_id else "/admin/roles"


def _version(form: FormData) -> int:
    """Return the version the form was rendered with; 0 (never current) when missing."""
    raw = form.text("version")
    return int(raw) if raw.isdigit() and len(raw) <= 9 else 0


@router.get("/roles", response_class=HTMLResponse)
async def overview(request: Request, locale: Locale, context: Manage) -> HTMLResponse:
    """List the custom roles and offer a template to copy."""
    _ = locale.gettext
    catalogue = await role_service(request).catalogue()
    rows = [
        RoleRow(role, role.name(locale.code), _(ROLE_LABELS[role.template]))
        for role in catalogue.custom
    ]
    return _page(
        request,
        locale,
        "admin/roles.html",
        context.principal,
        {
            "roles": rows,
            "templates": [(role.value, _(ROLE_LABELS[role])) for role in TEMPLATES],
            "done": done_message(request, _),
        },
    )


@router.get("/roles/new", response_class=HTMLResponse)
async def new_role(
    request: Request, locale: Locale, context: Manage, template: str = ""
) -> HTMLResponse:
    """Show the editor prefilled with the permissions of ``template``."""
    form = FormData({}, locale.gettext)
    return _form_page(request, locale, context, form, _template(template))


@router.post("/roles", response_class=HTMLResponse)
async def create_role(request: Request, locale: Locale, _: SameOrigin, context: Manage) -> Response:
    """Create the role and go to its page."""
    form = await read_form(request, locale)
    template = _template(form.text("template"))
    try:
        role = await role_service(request).create(context.principal, _draft(form, template))
    except DomainError as error:
        _refused(locale, form, error)
        return _form_page(request, locale, context, form, template, status=422)
    return redirect_done(_url(role.id), "created")


@router.get("/roles/{role_id}", response_class=HTMLResponse)
async def role_detail(
    request: Request, locale: Locale, role_id: int, context: Manage
) -> HTMLResponse:
    """Show the stored definition in the editor."""
    current = await role_service(request).role(context.principal, role_id)
    form = FormData({}, locale.gettext)
    return _form_page(request, locale, context, form, current.template, current)


@router.post("/roles/{role_id}", response_class=HTMLResponse)
async def update_role(
    request: Request, locale: Locale, role_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Save names, claim value and permissions; the template stays."""
    service = role_service(request)
    current = await service.role(context.principal, role_id)
    form = await read_form(request, locale)
    try:
        changed = await service.update(
            context.principal, role_id, _draft(form, current.template), _version(form)
        )
    except DomainError as error:
        _refused(locale, form, error)
        return _form_page(request, locale, context, form, current.template, current, 422)
    return redirect_done(_url(role_id), "saved" if changed else "unchanged")


@router.post("/roles/{role_id}/delete", response_class=HTMLResponse)
async def delete_role(
    request: Request, locale: Locale, role_id: int, _: SameOrigin, context: Manage
) -> Response:
    """Delete the role; its holders keep no role from it."""
    service = role_service(request)
    current = await service.role(context.principal, role_id)
    form = await read_form(request, locale)
    try:
        await service.delete(context.principal, role_id, _version(form))
    except DomainError as error:
        shown = FormData({}, locale.gettext)  # the editor shows the stored definition
        _refused(locale, shown, error)
        return _form_page(request, locale, context, shown, current.template, current, 409)
    return redirect_done(_url(), "removed")
