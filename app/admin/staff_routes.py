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

A person's page also lists the entitlement values of their last login that this installation
did not recognise, with the reason (ADR 0049 point 8), within the viewer's scope.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.admin.audit_routes import audit_views
from app.admin.authz import AdminContext, RequirePermission, admin_service
from app.admin.forms import Option
from app.admin.idp_mapping_routes import reason_label
from app.admin.pages import Locale, admin_moment
from app.admin.pages import page as _page
from app.domain.audit import AuditFilter
from app.domain.authorization import Permission, Role, scope_for
from app.domain.scheduling import GroupSummary
from app.domain.staff import StaffFilter, StaffMember
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
    return StaffView(
        id=member.id or 0,
        name=member.display_name,
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
        dormant=member.is_dormant(now, _dormant_after(request)),
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
) -> StaffFilter:
    """Turn the query parameters into a filter; unknown or out-of-scope values are ignored."""
    return StaffFilter(
        organisation=organisation
        if organisation in {g.organisation_slug for g in in_scope}
        else None,
        group=group if group in {g.group.slug for g in in_scope} else None,
        role=Role(role) if role in set(Role) else None,
        dormant_before=dormant_before if dormant == "1" else None,
        unrecognised=unrecognised == "1",
    )


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
) -> HTMLResponse:
    """List the people who use the portal, within the viewer's scope."""
    _ = locale.gettext
    service = admin_service(request)
    now = request.app.state.clock.now()
    in_scope = await service.groups(context.principal, Permission.STAFF_VIEW)
    narrow = _narrow(
        organisation, group, role, dormant, in_scope, now - _dormant_after(request), unrecognised
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
    )
    return _page(
        request,
        locale,
        "admin/staff.html",
        context.principal,
        {
            "people": [_view(request, locale, m, names, now) for m in members],
            "filters": filters,
            "dormant_days": settings_for(request).staff_dormant_after_days,
        },
    )


@router.get("/staff/{member_id}", response_class=HTMLResponse)
async def staff_member(
    request: Request,
    locale: Locale,
    member_id: int,
    context: Annotated[AdminContext, Depends(RequirePermission(Permission.STAFF_VIEW))],
) -> HTMLResponse:
    """Show one person and their recent activity; outside the scope it does not exist (404)."""
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
    names = _names(await service.groups(context.principal, Permission.STAFF_VIEW))
    now = request.app.state.clock.now()
    return _page(
        request,
        locale,
        "admin/staff_member.html",
        context.principal,
        {
            "person": _view(request, locale, member, names, now),
            "events": activity,
            "activity_limit": ACTIVITY_LIMIT,
        },
    )
