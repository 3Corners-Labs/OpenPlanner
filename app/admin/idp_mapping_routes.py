"""The identity provider mapping page and its download (ADR 0049 point 17).

Read-only: the exact entitlement value per role per organisation and group within the
viewer's scope, who last held each one, and the values refused in the window. The download
is the same list as a text file for the IdP team, audited. Editing a group's IdP value is a
form on the group's settings page (``group_routes``), built here (``group_idp_view``) so the
page and the form share one translation of the data.

    GET /admin/idp-mapping             idp_mapping.view
    GET /admin/idp-mapping/export.txt  idp_mapping.view, audited idp_mapping.exported
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import Field, FormData
from app.admin.pages import Locale, admin_moment, page
from app.domain.authorization import Permission, Role, Target, decide
from app.domain.entitlements import IDP_KEY_PATTERN, ClaimMode, RefusalReason
from app.domain.idp_mapping import EntitlementLine, IdpMappingService, MappingOverview
from app.domain.ports import Clock
from app.web.authz_labels import ROLE_LABELS
from app.web.locale import RequestLocale

router = APIRouter(prefix="/admin", include_in_schema=False)

URL = "/admin/idp-mapping"

REASON_LABELS: dict[str, str] = {
    RefusalReason.MALFORMED: "admin.idp_mapping.reason.malformed",
    RefusalReason.UNKNOWN_ROLE: "admin.idp_mapping.reason.unknown_role",
    RefusalReason.UNKNOWN_ORGANISATION: "admin.idp_mapping.reason.unknown_organisation",
    RefusalReason.UNKNOWN_GROUP: "admin.idp_mapping.reason.unknown_group",
    RefusalReason.GROUP_NOT_IN_ORGANISATION: "admin.idp_mapping.reason.group_not_in_organisation",
    RefusalReason.SCOPE_NOT_ALLOWED_FOR_ROLE: "admin.idp_mapping.reason.scope_not_allowed",
}
"""Readable label per refusal reason code (ADR 0049 point 5)."""

MODE_LABELS: dict[ClaimMode, str] = {
    ClaimMode.LEGACY: "admin.idp_mapping.mode.legacy",
    ClaimMode.SHADOW: "admin.idp_mapping.mode.shadow",
    ClaimMode.ENTITLEMENTS: "admin.idp_mapping.mode.entitlements",
}
"""Readable label per claim mode (ADR 0049 point 13)."""

View = Annotated[AdminContext, Depends(RequirePermission(Permission.IDP_MAPPING_VIEW))]


def idp_mapping_service(request: Request) -> IdpMappingService:
    """Return the application's identity provider mapping service."""
    service: IdpMappingService = request.app.state.idp_mapping_service
    return service


def reason_label(reason: str, locale: RequestLocale) -> str:
    """Translate a refusal reason code; an unknown code is shown as the code."""
    key = REASON_LABELS.get(reason)
    return locale.gettext(key) if key else reason


@dataclass(frozen=True)
class LineView:
    """One entitlement value with its role label and who holds it."""

    role: str
    value: str
    holders: int
    last_login: str


@dataclass(frozen=True)
class GroupView:
    """A group with its IdP value and lines."""

    name: str
    slug: str
    idp_key: str
    lines: tuple[LineView, ...]


@dataclass(frozen=True)
class OrganisationView:
    """An organisation with its IdP value, lines and groups."""

    name: str
    slug: str
    idp_key: str
    lines: tuple[LineView, ...]
    groups: tuple[GroupView, ...]


@dataclass(frozen=True)
class RefusedView:
    """One refused value as the page lists it."""

    value: str
    reason: str
    organisation: str
    received: str
    people: int


@dataclass(frozen=True)
class GroupIdpView:
    """The IdP value form on a group's settings page.

    Attributes:
        value: The current or submitted value.
        can_manage: Whether the viewer may change it (``idp_mapping.manage``).
        holders: How many people last held a grant on the group (lose access on a change).
        field: The input, for the field macro (with its manual section attached by the
            group routes); ``None`` when the viewer may not change the value.
    """

    value: str
    can_manage: bool
    holders: int
    field: Field | None = None


def _role_label(key: str, overview: MappingOverview, locale: RequestLocale) -> str:
    """Name a role key: built-in ones translated, custom ones by their own name."""
    if key in {r.value for r in Role}:
        return locale.gettext(ROLE_LABELS[Role(key)])
    custom = next((r for r in overview.custom_roles if r.key == key), None)
    return custom.name(locale.code) if custom else key


def _moment(request: Request, locale: RequestLocale, at: datetime | None) -> str:
    """A compact moment, or "never"."""
    return admin_moment(request, locale, at) if at else locale.gettext("admin.idp_mapping.never")


def _views(
    request: Request, locale: RequestLocale, overview: MappingOverview
) -> tuple[list[OrganisationView], list[RefusedView]]:
    """Translate the overview for the template."""

    def lines(found: Sequence[EntitlementLine]) -> tuple[LineView, ...]:
        return tuple(
            LineView(
                _role_label(line.role, overview, locale),
                line.value,
                line.holders,
                _moment(request, locale, line.last_login_at),
            )
            for line in found
        )

    organisations = [
        OrganisationView(
            o.name,
            o.slug,
            o.idp_key,
            lines(o.lines),
            tuple(GroupView(g.name, g.slug, g.idp_key, lines(g.lines)) for g in o.groups),
        )
        for o in overview.organisations
    ]
    refused = [
        RefusedView(
            r.value,
            reason_label(r.reason, locale),
            r.organisation or locale.gettext("admin.idp_mapping.no_organisation"),
            admin_moment(request, locale, r.received_at),
            r.people,
        )
        for r in overview.refused
    ]
    return organisations, refused


@dataclass(frozen=True)
class ProviderView:
    """Which identity provider the platform logs in with, for the top of the page."""

    kind: str
    issuer: str
    staff: str


def _provider(request: Request, locale: Locale) -> ProviderView | None:
    """Describe the platform's provider; ``None`` while the portal has no login configured.

    The issuer is the address the provider announces (a public value, ADR 0097); no secret
    and no client identifier is shown.
    """
    _ = locale.gettext
    settings = request.app.state.settings
    if settings.oidc_discovery_url is None:
        return None
    issuer = settings.oidc_issuer or settings.oidc_discovery_url.removesuffix(
        "/.well-known/openid-configuration"
    )
    service = request.app.state.staff_directory_service
    if not settings.keycloak_admin_url:
        kind, staff = _("admin.idp_mapping.provider.own"), _("admin.idp_mapping.staff.at_provider")
    elif service.available:
        kind, staff = (
            _("admin.idp_mapping.provider.bundled"),
            _("admin.idp_mapping.staff.in_portal"),
        )
    else:
        kind, staff = (
            _("admin.idp_mapping.provider.bundled"),
            _("admin.idp_mapping.staff.off"),
        )
    return ProviderView(kind, issuer, staff)


@router.get("/idp-mapping", response_class=HTMLResponse)
async def idp_mapping(request: Request, locale: Locale, context: View) -> HTMLResponse:
    """Show the values the identity provider must send, within the viewer's scope."""
    overview = await idp_mapping_service(request).overview(context.principal)
    organisations, refused = _views(request, locale, overview)
    return page(
        request,
        locale,
        "admin/idp_mapping.html",
        context.principal,
        {
            "settings": overview.settings,
            "mode_label": locale.gettext(MODE_LABELS[overview.settings.mode]),
            "superuser_value": overview.superuser_value,
            "provider": _provider(request, locale),
            "organisations": organisations,
            "refused": refused,
        },
    )


@router.get("/idp-mapping/export.txt", response_model=None)
async def export_idp_mapping(request: Request, locale: Locale, context: View) -> Response:
    """Download the values as a text file, one per line; the download is audited."""
    document = await idp_mapping_service(request).export(context.principal)
    clock: Clock = request.app.state.clock
    name = f"openplanner-entitlements-{clock.now():%Y%m%d}.txt"
    return Response(
        document.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "content-disposition": f'attachment; filename="{name}"',
            "cache-control": "no-store",
        },
    )


async def group_idp_view(
    request: Request, locale: RequestLocale, context: AdminContext, form: FormData | None = None
) -> GroupIdpView:
    """Build the IdP value form of the group's settings page (ADR 0049 point 4)."""
    summary = context.group
    target = Target.for_organisation(summary.organisation_slug)
    can_manage = decide(context.principal, Permission.IDP_MAPPING_MANAGE, target).allowed
    holders = 0
    if can_manage:
        holders = await idp_mapping_service(request).group_holders(
            context.principal, summary.group.slug
        )
    value = str(
        form.values.get("idp_key", summary.group.idp_key) if form else summary.group.idp_key
    )
    if not can_manage:
        return GroupIdpView(value, False, 0)
    _ = locale.gettext
    hint = _("admin.group.idp_key.hint")
    if holders:
        hint = f"{hint} {_('admin.idp_mapping.holders_warning', count=holders)}"
    field = Field(
        "idp_key",
        _("admin.idp_mapping.idp_key"),
        value=value,
        hint=hint,
        error=form.error_for("idp_key") if form else None,
        required=True,
        attributes={
            "autocapitalize": "none",
            "spellcheck": "false",
            "pattern": IDP_KEY_PATTERN,
            "maxlength": "63",
        },
    )
    return GroupIdpView(value, True, holders, field)
