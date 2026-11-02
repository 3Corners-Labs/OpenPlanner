"""Managing staff in the bundled identity provider from the portal (ADR 0095).

The Staff screen stays the one home (ADR 0047): this module adds the invitation form under it
and the actions on a person. Nothing here exists for a provider without the ``StaffDirectory``
capability; the screens then show no action and the routes answer 403 like any refusal.

    GET  /admin/staff/invite                 staff.manage
    POST /admin/staff/invite                 staff.manage, fresh login
    POST /admin/staff/directory/grants/add   staff.manage, fresh login
    POST /admin/staff/directory/grants/remove  staff.manage, fresh login
    POST /admin/staff/directory/enabled      staff.manage, fresh login
    POST /admin/staff/directory/reset        staff.reset_password, fresh login

A person is addressed by a hidden form field, never in the URL: the provider's identifier
stays out of links and logs. Every refusal comes back as one message with a code, never with
what the provider said.
"""

import re
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.admin.authz import AdminContext, RequirePermission, admin_service
from app.admin.forms import (
    Field,
    FormData,
    Option,
    raise_refusal,
    read_form,
    redirect_done,
)
from app.admin.pages import Locale, SameOrigin
from app.admin.pages import page as _page
from app.domain.authorization import Grant, Permission, Principal, Role
from app.domain.errors import DomainError
from app.domain.scheduling import GroupSummary
from app.domain.staff_directory import NewStaffMember
from app.domain.staff_directory_service import ManagedPerson, StaffDirectoryService
from app.web.authz_labels import ROLE_LABELS
from app.web.errors import FormError
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

INVITE_URL = "/admin/staff/invite"
STAFF_URL = "/admin/staff"
_MANAGE = Annotated[AdminContext, Depends(RequirePermission(Permission.STAFF_MANAGE, fresh=True))]
_RESET = Annotated[
    AdminContext, Depends(RequirePermission(Permission.STAFF_RESET_PASSWORD, fresh=True))
]
_BACK = re.compile(r"/admin/staff(?:/\d{1,9})?")
"""Where an action may return to: the list or one person's page, nothing else."""
_REQUIRED = {
    "email": "admin.staff.invite.error.email",
    "first_name": "admin.staff.invite.error.first_name",
    "last_name": "admin.staff.invite.error.last_name",
}
"""The inputs an invitation needs, with the message when one is missing."""
_GRANTABLE = (Role.ORG_ADMIN, Role.GROUP_ADMIN, Role.AUDITOR, Role.AGENT)
"""Roles an admin may hand out; the superuser role is added for the superuser."""


def directory_service(request: Request) -> StaffDirectoryService:
    """Return the application's staff directory service."""
    service: StaffDirectoryService = request.app.state.staff_directory_service
    return service


@dataclass(frozen=True)
class GrantView:
    """One grant of a person, for the table with its remove button."""

    key: str
    role: str
    place: str


@dataclass(frozen=True)
class ManageView:
    """The actions card on a person's page, or a row of the invited-people table."""

    subject: str
    email: str
    enabled: bool
    grants: tuple[GrantView, ...]
    works_elsewhere: bool
    can_reset: bool


def grant_from_key(key: str) -> Grant | None:
    """Parse ``Grant.key`` (``org_admin:demo``, ``agent:demo/team``, ``superuser``)."""
    role, _, place = key.partition(":")
    if not role:
        return None
    organisation, _, group = place.partition("/")
    return Grant(role, organisation or None, group or None)


def _places(groups: list[GroupSummary]) -> dict[str, str]:
    """Organisation and group names by slug, from the groups in the admin's scope."""
    names = {g.organisation_slug: g.organisation_name for g in groups}
    names.update({g.group.slug: g.group.name for g in groups})
    return names


def _place_label(grant: Grant, names: dict[str, str], whole: str) -> str:
    """Describe where a grant applies."""
    if grant.organisation is None:
        return whole
    organisation = names.get(grant.organisation, grant.organisation)
    if grant.group is None:
        return organisation
    return f"{organisation} / {names.get(grant.group, grant.group)}"


def manage_view(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    person: ManagedPerson,
    groups: list[GroupSummary],
) -> ManageView:
    """Translate a managed person for the templates."""
    _ = locale.gettext
    names = _places(groups)
    can = directory_service(request).available
    return ManageView(
        person.user.id,
        person.user.email,
        person.user.enabled,
        tuple(
            GrantView(
                g.key,
                _(ROLE_LABELS[Role(g.role)]) if g.role in {r.value for r in Role} else g.role,
                _place_label(g, names, _("admin.staff.manage.whole_platform")),
            )
            for g in person.grants
        ),
        person.works_elsewhere,
        can,
    )


def _role_options(principal: Principal, locale: RequestLocale) -> tuple[Option, ...]:
    """The roles this admin may hand out, labelled."""
    _ = locale.gettext
    roles = ((Role.SUPERUSER,) if Role.SUPERUSER in principal.roles else ()) + _GRANTABLE
    return tuple(Option(r.value, _(ROLE_LABELS[r])) for r in roles)


def _place_options(
    principal: Principal, locale: RequestLocale, groups: list[GroupSummary]
) -> tuple[Option, ...]:
    """Where a grant may apply: the whole platform (superuser), organisations, groups."""
    _ = locale.gettext
    options: list[Option] = []
    if Role.SUPERUSER in principal.roles:
        options.append(Option("", _("admin.staff.manage.whole_platform")))
    seen: set[str] = set()
    for g in groups:
        if g.organisation_slug not in seen:
            seen.add(g.organisation_slug)
            options.append(Option(g.organisation_slug, g.organisation_name))
    options += [
        Option(f"{g.organisation_slug}/{g.group.slug}", f"{g.organisation_name} / {g.group.name}")
        for g in groups
    ]
    return tuple(options)


def add_fields(
    principal: Principal, locale: RequestLocale, groups: list[GroupSummary], form: FormData
) -> list[Field]:
    """The role and place inputs shared by the invitation and the *add a role* form."""
    _ = locale.gettext
    return [
        Field(
            "role",
            _("admin.staff.manage.role"),
            "select",
            form.text("role"),
            error=form.error_for("role"),
            options=_role_options(principal, locale),
        ),
        Field(
            "place",
            _("admin.staff.manage.place"),
            "select",
            form.text("place"),
            hint=_("admin.staff.manage.place_hint"),
            error=form.error_for("place"),
            options=_place_options(principal, locale, groups),
            required=False,
        ),
    ]


def wish_of(form: FormData) -> Grant | None:
    """Read the role and place of a form as a grant; the domain judges whether it may be."""
    role = form.text("role")
    place = form.text("place")
    if not role:
        return None
    organisation, _, group = place.partition("/")
    return Grant(role, organisation or None, group or None)


def _invite_fields(
    principal: Principal, locale: RequestLocale, groups: list[GroupSummary], form: FormData
) -> list[Field]:
    """The invitation form: who, and the first role and place."""
    _ = locale.gettext
    return [
        Field(
            "email",
            _("admin.staff.invite.email"),
            value=form.text("email"),
            error=form.error_for("email"),
            attributes={"type": "email", "autocomplete": "off", "spellcheck": "false"},
        ),
        Field(
            "first_name",
            _("admin.staff.invite.first_name"),
            value=form.text("first_name"),
            error=form.error_for("first_name"),
            attributes={"autocomplete": "off"},
        ),
        Field(
            "last_name",
            _("admin.staff.invite.last_name"),
            value=form.text("last_name"),
            error=form.error_for("last_name"),
            attributes={"autocomplete": "off"},
        ),
        *add_fields(principal, locale, groups, form),
    ]


async def _invite_response(
    request: Request, locale: RequestLocale, context: AdminContext, form: FormData, status: int
) -> HTMLResponse:
    """Render the invitation form."""
    groups = await admin_service(request).groups(context.principal, Permission.STAFF_MANAGE)
    response = _page(
        request,
        locale,
        "admin/staff_invite.html",
        context.principal,
        {
            "fields": _invite_fields(context.principal, locale, groups, form),
            "errors": form.errors,
            "days": request.app.state.settings.staff_invite_expiry_days,
        },
    )
    response.status_code = status
    return response


@router.get("/staff/invite", response_class=HTMLResponse)
async def invite_form(request: Request, locale: Locale, context: _MANAGE) -> Response:
    """Show the invitation form; without a directory the staff list is the better place."""
    if not directory_service(request).available:
        return RedirectResponse(STAFF_URL, status_code=303)
    return await _invite_response(request, locale, context, FormData({}, locale.gettext), 200)


@router.post("/staff/invite", response_class=HTMLResponse)
async def invite(request: Request, locale: Locale, _: SameOrigin, context: _MANAGE) -> Response:
    """Create the person, give them the first role and mail them the invitation."""
    form = await read_form(request, locale)
    wish = wish_of(form)
    for name, message in _REQUIRED.items():
        if not form.text(name) or (name == "email" and "@" not in form.text(name)):
            form.errors.append(FormError(name, locale.gettext(message)))
    if wish is None:
        form.errors.append(FormError("role", locale.gettext("admin.staff.invite.error.role")))
    if not form.errors and wish is not None:
        new = NewStaffMember(form.text("email"), form.text("first_name"), form.text("last_name"))
        try:
            await directory_service(request).invite(context.principal, new, [wish])
        except DomainError as error:
            raise_refusal(error)
            form.refusal(error)
        else:
            return redirect_done(STAFF_URL, "staff_invited")
    return await _invite_response(request, locale, context, form, 422)


async def _act(
    request: Request,
    locale: RequestLocale,
    principal: Principal,
    back: str,
    done: str,
    action: Awaitable[None],
) -> Response:
    """Run one action on a person; a refusal is a page with its one message."""
    try:
        await action
    except DomainError as error:
        raise_refusal(error)
        form = FormData({}, locale.gettext)
        form.refusal(error)
        return _page(
            request,
            locale,
            "admin/staff_directory_error.html",
            principal,
            {"errors": form.errors, "back": back},
        )
    return redirect_done(back, done)


async def _subject_and_back(request: Request, locale: RequestLocale) -> tuple[FormData, str, str]:
    """Read the person and where to return to (the list or the person's page)."""
    form = await read_form(request, locale)
    back = form.text("back")
    return form, form.text("subject"), back if _BACK.fullmatch(back) else STAFF_URL


@router.post("/staff/directory/grants/add", response_class=HTMLResponse)
async def grant_role(request: Request, locale: Locale, _: SameOrigin, context: _MANAGE) -> Response:
    """Add one role and place to a person, keeping what they already hold in scope."""
    form, subject, back = await _subject_and_back(request, locale)
    service = directory_service(request)
    person = await service.person(context.principal, subject)
    wish = wish_of(form)
    if person is None or wish is None:
        return redirect_done(back, "staff_unchanged")
    return await _act(
        request,
        locale,
        context.principal,
        back,
        "staff_grants_saved",
        service.set_grants(context.principal, subject, [*person.grants, wish]),
    )


@router.post("/staff/directory/grants/remove", response_class=HTMLResponse)
async def remove_grant(
    request: Request, locale: Locale, _: SameOrigin, context: _MANAGE
) -> Response:
    """Remove one role and place from a person."""
    form, subject, back = await _subject_and_back(request, locale)
    service = directory_service(request)
    person = await service.person(context.principal, subject)
    if person is None:
        return redirect_done(back, "staff_unchanged")
    gone = grant_from_key(form.text("grant"))
    return await _act(
        request,
        locale,
        context.principal,
        back,
        "staff_grants_saved",
        service.set_grants(context.principal, subject, [g for g in person.grants if g != gone]),
    )


@router.post("/staff/directory/enabled", response_class=HTMLResponse)
async def set_enabled(
    request: Request, locale: Locale, _: SameOrigin, context: _MANAGE
) -> Response:
    """Enable or disable a person."""
    form, subject, back = await _subject_and_back(request, locale)
    enabled = form.text("enabled") == "yes"
    return await _act(
        request,
        locale,
        context.principal,
        back,
        "staff_enabled" if enabled else "staff_disabled",
        directory_service(request).set_enabled(context.principal, subject, enabled),
    )


@router.post("/staff/directory/reset", response_class=HTMLResponse)
async def send_reset(request: Request, locale: Locale, _: SameOrigin, context: _RESET) -> Response:
    """Mail a person a link to choose a new password."""
    _form, subject, back = await _subject_and_back(request, locale)
    return await _act(
        request,
        locale,
        context.principal,
        back,
        "staff_reset_sent",
        directory_service(request).send_reset(context.principal, subject),
    )
