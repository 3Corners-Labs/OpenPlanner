"""The staff directory pages: who uses the admin portal, with what roles and where (ADR 0046).

The list shows each person the viewer may see (``staff.view``, exact per scope) with their
roles, the organisations and groups those roles reach (only those inside the viewer's
scope), and when they last logged in, were active and logged out. People who have not logged
in for ``STAFF_DORMANT_AFTER_DAYS`` are marked dormant, in words and not by colour alone. The
filters are a GET form without JavaScript; unknown or out-of-scope values are ignored.

A person's page adds their recent activity from the audit log, within the viewer's audit
scope. Links use the record's number, never the identity-provider subject.

    GET /admin/staff?organisation=demo&group=team&role=org_admin&dormant=1&unrecognised=1
                                                                               staff.view
    GET /admin/staff/{member_id}                                               staff.view

A suspended or erased person carries a chip in words (ADR 0046 steps 2); an erased one shows
no name, only "Erased staff member". The actions on a person (suspend, reinstate, erase) are
in ``app.admin.staff_status_routes``.

A person's page also shows the skills they hold, per organisation they belong to (ADR 0121):
as a list under ``staff.view``, as checkboxes for an administrator with ``skills.manage`` in
that organisation:

    POST /admin/staff/{member_id}/skills/{org}                 skills.manage, fresh login

The save replaces the skills the person holds in that one organisation (their skills elsewhere
stay). The route depends on ``skills.manage`` in the organisation; ``SkillService`` checks the
matrix again, that the viewer may see the person (``staff.view``), that the person belongs to
the organisation for every skill that is added, and audits the change with skill codes and the
person's number, never a name. A person the viewer may not see, or an organisation they have no
right in, answers like an unknown address. A change applies from the next request that is picked
up or started.

A person's page also lists the entitlement values of their last login that this installation
did not recognise, with the reason (ADR 0049 point 8), within the viewer's scope.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.audit_routes import audit_views
from app.admin.authz import AdminContext, RequirePermission, admin_service, skill_service
from app.admin.forms import FormData, Option, done_message, read_form, redirect_done
from app.admin.idp_mapping_routes import reason_label
from app.admin.pages import Locale, SameOrigin, admin_moment
from app.admin.pages import page as _page
from app.admin.skill_inputs import (
    SkillChoice,
    choices,
    chosen_ids,
    raise_unknown,
    refuse,
    ticked_ids,
)
from app.admin.staff_directory_routes import (
    add_fields,
    directory_service,
    manage_view,
)
from app.domain.audit import AuditFilter
from app.domain.authorization import Permission, Principal, Role, scope_for
from app.domain.errors import DomainError, ErrorCode
from app.domain.identity import PLATFORM
from app.domain.scheduling import GroupSummary
from app.domain.staff import StaffFilter, StaffMember, StaffStatus
from app.web.authz_labels import ROLE_LABELS
from app.web.locale import RequestLocale
from app.web.organisation_settings import settings_for
from app.web.protection import RejectedRequest

router = APIRouter(prefix="/admin", include_in_schema=False)

ACTIVITY_LIMIT = 50
"""How many recent audit events a person's page shows; the full log has the rest."""


@dataclass(frozen=True)
class PlaceView:
    """One organisation of a person, with the groups they reach in it."""

    organisation: str
    groups: tuple[str, ...]


@dataclass(frozen=True)
class RefusedView:
    """One entitlement value of the person's last login that gave no right (ADR 0049)."""

    value: str
    reason: str
    received: str


@dataclass(frozen=True)
class StaffView:
    """A person as the list and their page show them."""

    id: int
    name: str
    roles: tuple[str, ...]
    places: tuple[PlaceView, ...]
    first_seen: str
    last_login: str
    last_seen: str
    last_logout: str
    dormant: bool
    refused: tuple[RefusedView, ...] = ()
    suspended: bool = False
    erased: bool = False
    provider: str = ""
    """The identity provider the person logs in through: a provider key, or the platform."""
    email: str = ""
    """The verified address of the last login; empty when none (ADR 0117 step 10b)."""


PLATFORM_VALUE = "platform"
"""The filter's value for the platform provider, whose key is empty (ADR 0083 point 16)."""


@dataclass(frozen=True)
class StaffFilterView:
    """The filter form: the choices and what is selected now."""

    organisations: tuple[Option, ...]
    organisation: str
    groups: tuple[Option, ...]
    group: str
    roles: tuple[Option, ...]
    role: str
    dormant: bool
    unrecognised: bool
    active: bool
    providers: tuple[Option, ...] = ()
    provider: str = ""


def _dormant_after(request: Request) -> timedelta:
    """The period without a login after which a person is dormant (ADR 0046 setting)."""
    return timedelta(days=settings_for(request).staff_dormant_after_days)


def _names(in_scope: list[GroupSummary]) -> tuple[dict[str, str], dict[str, str]]:
    """Organisation and group names by slug, from the groups in the viewer's scope."""
    organisations = {g.organisation_slug: g.organisation_name for g in in_scope}
    groups = {g.group.slug: g.group.name for g in in_scope}
    return organisations, groups


def _view(
    request: Request,
    locale: RequestLocale,
    member: StaffMember,
    names: tuple[dict[str, str], dict[str, str]],
    now: datetime,
) -> StaffView:
    """Translate a person for the templates; unknown slugs are shown as the slug."""
    _ = locale.gettext
    organisations, groups = names
    never = _("admin.staff.never")
    erased = member.status is StaffStatus.ERASED
    return StaffView(
        id=member.id or 0,
        name=_("admin.staff.erased_name") if erased else member.display_name,
        roles=tuple(_(ROLE_LABELS[r]) for r in Role if r in member.roles),
        places=tuple(
            PlaceView(
                organisations.get(org, org),
                tuple(groups.get(g, g) for g in member.groups_of(org)),
            )
            for org in member.organisations
        ),
        first_seen=admin_moment(request, locale, member.first_seen_at),
        last_login=admin_moment(request, locale, member.last_login_at),
        last_seen=admin_moment(request, locale, member.last_seen_at),
        last_logout=(
            admin_moment(request, locale, member.last_logout_at) if member.last_logout_at else never
        ),
        provider=member.provider or _("admin.staff.platform_provider"),
        email="" if erased else member.email or "",
        dormant=not erased and member.is_dormant(now, _dormant_after(request)),
        suspended=member.status is StaffStatus.SUSPENDED,
        erased=erased,
        refused=tuple(
            RefusedView(
                r.value,
                reason_label(r.reason, locale),
                admin_moment(request, locale, r.received_at),
            )
            for r in member.refused
        ),
    )


def _narrow(
    organisation: str | None,
    group: str | None,
    role: str | None,
    dormant: str | None,
    in_scope: list[GroupSummary],
    dormant_before: datetime,
    unrecognised: str | None = None,
    provider: str | None = None,
    providers: frozenset[str] = frozenset(),
) -> StaffFilter:
    """Turn the query parameters into a filter; unknown or out-of-scope values are ignored.

    ``provider`` is ``platform`` or a provider key among ``providers`` (ADR 0083 point 16).
    """
    chosen = PLATFORM if provider == PLATFORM_VALUE else provider
    return StaffFilter(
        provider=chosen if chosen in providers else None,
        organisation=organisation
        if organisation in {g.organisation_slug for g in in_scope}
        else None,
        group=group if group in {g.group.slug for g in in_scope} else None,
        role=Role(role) if role in set(Role) else None,
        dormant_before=dormant_before if dormant == "1" else None,
        unrecognised=unrecognised == "1",
    )


def _directory_notice(
    locale: RequestLocale, missing: tuple[str, ...], unreachable: bool
) -> str | None:
    """Say why the bundled provider's actions are missing, naming settings, never values.

    ADR 0106 point 3: a directory that is half configured or does not answer is shown, never
    silent. A provider without a directory by design (ADR 0095 point 1) gets no notice.
    """
    _ = locale.gettext
    if missing:
        return _("admin.staff.directory.missing", names=", ".join(missing))
    if unreachable:
        return _("admin.staff.directory.unreachable")
    return None


@router.get("/staff", response_class=HTMLResponse)
async def staff_list(
    request: Request,
    locale: Locale,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.STAFF_VIEW))],
    organisation: str | None = None,
    group: str | None = None,
    role: str | None = None,
    dormant: str | None = None,
    unrecognised: str | None = None,
    provider: str | None = None,
) -> HTMLResponse:
    """List the people who use the portal, within the viewer's scope."""
    _ = locale.gettext
    service = admin_service(request)
    now = request.app.state.clock.now()
    in_scope = await service.groups(context.principal, Permission.STAFF_VIEW)
    providers = frozenset(m.provider for m in await service.staff(context.principal, StaffFilter()))
    narrow = _narrow(
        organisation,
        group,
        role,
        dormant,
        in_scope,
        now - _dormant_after(request),
        unrecognised,
        provider,
        providers,
    )
    members = await service.staff(context.principal, narrow)
    names = _names(in_scope)
    filters = StaffFilterView(
        organisations=tuple(Option(slug, name) for slug, name in sorted(names[0].items())),
        organisation=narrow.organisation or "",
        groups=tuple(Option(g.group.slug, f"{g.group.name} ({g.group.slug})") for g in in_scope),
        group=narrow.group or "",
        roles=tuple(Option(r.value, _(ROLE_LABELS[r])) for r in Role),
        role=narrow.role.value if narrow.role else "",
        dormant=narrow.dormant_before is not None,
        unrecognised=narrow.unrecognised,
        active=narrow != StaffFilter(),
        providers=tuple(
            Option(key or PLATFORM_VALUE, key or _("admin.staff.platform_provider"))
            for key in sorted(providers)
        ),
        provider=(PLATFORM_VALUE if narrow.provider == PLATFORM else narrow.provider or ""),
    )
    directory = directory_service(request)
    manager = not scope_for(context.principal, Permission.STAFF_MANAGE).is_empty
    try:
        pending = await directory.pending(context.principal)
        unreachable = False
    except DomainError as error:
        if error.code is not ErrorCode.STAFF_DIRECTORY_UNAVAILABLE:
            raise
        pending, unreachable = [], True
    return _page(
        request,
        locale,
        "admin/staff.html",
        context.principal,
        {
            "people": [_view(request, locale, m, names, now) for m in members],
            "can_invite": directory.available and manager and not unreachable,
            "directory_notice": _directory_notice(locale, directory.missing, unreachable)
            if manager
            else None,
            "pending": [manage_view(request, locale, context, p, in_scope) for p in pending],
            "filters": filters,
            "dormant_days": settings_for(request).staff_dormant_after_days,
        },
    )


SKILLS_DONE = ("staff_skills_saved", "unchanged")
"""The confirmations of a save of the skills, shown in that section of the page."""

SkillsManage = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.SKILLS_MANAGE, organisation_param="org", fresh=True)),
]
"""The permission of the skills of a person in one organisation."""


@dataclass(frozen=True)
class SkillSection:
    """One organisation's skills of a person, as the staff page shows them.

    Attributes:
        slug: The organisation's slug; the section's form and anchor are named by it.
        name: The organisation's name.
        choices: The skills as checkboxes (those held ticked).
        held: Labels of the skills the person holds, for a viewer who may not change them.
        editable: Whether the viewer may change them (``skills.manage``, ADR 0121).
        error: The refusal of the last save of this section, if any.
    """

    slug: str
    name: str
    choices: tuple[SkillChoice, ...]
    held: tuple[str, ...]
    editable: bool
    error: str | None = None


async def skill_sections(
    request: Request,
    locale: RequestLocale,
    principal: Principal,
    member_id: int,
    form: FormData | None = None,
    error_org: str | None = None,
) -> list[SkillSection]:
    """Return the person's skills per organisation; ``form`` keeps the ticks of a refused save.

    Raises:
        RejectedRequest: 404 for a person the viewer may not see.
    """
    try:
        found = await skill_service(request).staff_skills(principal, member_id)
    except DomainError as error:
        raise_unknown(error)
    sections = []
    for person in found:
        slug = person.organisation.slug
        refused = form is not None and slug == error_org
        ticked = ticked_ids(form) if form is not None and refused else person.held
        message = form.error_for(f"skills-{slug}") if form is not None and refused else None
        shown = choices(person.choices, ticked, locale.code)
        sections.append(
            SkillSection(
                slug,
                person.organisation.name,
                tuple(shown),
                tuple(
                    c.label for c in choices(person.choices, person.held, locale.code) if c.checked
                ),
                person.editable,
                message,
            )
        )
    return sections


async def member_page(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    member_id: int,
    *,
    form: FormData | None = None,
    error_org: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    """Render one person's page; outside the viewer's scope it does not exist (404).

    ``form`` and ``error_org`` re-show a refused save of the skills of that organisation.
    """
    service = admin_service(request)
    member = await service.staff_member(context.principal, member_id)
    if member is None:
        raise RejectedRequest(404)
    activity = []
    if not scope_for(context.principal, Permission.AUDIT_VIEW).is_empty:
        entries = await service.audit_log(
            context.principal, AuditFilter(actor=member.subject), ACTIVITY_LIMIT
        )
        activity = audit_views(request, locale, context.principal, entries)
    in_scope = await service.groups(context.principal, Permission.STAFF_VIEW)
    names = _names(in_scope)
    now = request.app.state.clock.now()
    managed = await directory_service(request).person(context.principal, member.subject)
    manage = manage_view(request, locale, context, managed, in_scope) if managed else None
    sections = (
        []
        if member.status is StaffStatus.ERASED
        else await skill_sections(request, locale, context.principal, member_id, form, error_org)
    )
    response = _page(
        request,
        locale,
        "admin/staff_member.html",
        context.principal,
        {
            "person": _view(request, locale, member, names, now),
            "events": activity,
            "manage": manage,
            "add_fields": add_fields(
                context.principal, locale, in_scope, FormData({}, locale.gettext)
            ),
            "activity_limit": ACTIVITY_LIMIT,
            "skill_sections": sections,
            "skills_done": done_message(request, locale.gettext, only=SKILLS_DONE),
            "errors": form.errors if form is not None else [],
        },
    )
    response.status_code = status
    return response


@router.get("/staff/{member_id}", response_class=HTMLResponse)
async def staff_member(
    request: Request,
    locale: Locale,
    member_id: int,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.STAFF_VIEW))],
) -> HTMLResponse:
    """Show one person and their recent activity; outside the scope it does not exist (404)."""
    return await member_page(request, locale, context, member_id)


@router.post("/staff/{member_id}/skills/{org}", response_model=None)
async def save_staff_skills(
    request: Request,
    locale: Locale,
    member_id: int,
    org: str,
    _: SameOrigin,
    context: SkillsManage,
) -> Response:
    """Replace the skills the person holds in ``org``; their skills elsewhere stay."""
    form = await read_form(request, locale)
    service = skill_service(request)
    try:
        changed = await service.grant(
            context.principal, org, member_id, chosen_ids(form, service.max_skills)
        )
    except DomainError as error:
        refuse(form, error, locale, f"skills-{org}")
        return await member_page(
            request, locale, context, member_id, form=form, error_org=org, status=422
        )
    return redirect_done(
        f"/admin/staff/{member_id}",
        "staff_skills_saved" if changed else "unchanged",
        anchor="skills",
    )
