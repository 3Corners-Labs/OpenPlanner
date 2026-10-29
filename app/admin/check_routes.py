"""The "Koppeling controleren" button of a group (verify button for its integration chain).

    POST /admin/groups/{slug}/check   group.edit (fresh)   run the check, then back to settings

The check reads Webex and WxCC and sends signed test events to our own webhook endpoints; it
changes nothing at the platforms. The latest result is shown on the group's settings page
(``check_view``). Platforms can be slow, so the button is limited per address.

It writes nothing but its own report, so it is the one ``maintenance_safe`` route: during
maintenance the superuser can still check a repaired integration (ADR 0038).
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.adapters.webhook_selftest import Outcome
from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import raise_refusal, redirect_done
from app.admin.integration_views import OUTCOME_LABELS
from app.admin.pages import SameOrigin, admin_moment
from app.domain.authorization import Permission
from app.domain.checks import Finding, GroupCheck
from app.domain.errors import DomainError
from app.domain.integration_check import IntegrationCheckService
from app.i18n import BoundGettext
from app.web.check_labels import AREA_LABELS, FINDING_LABELS, STATUS_LABELS, UNKNOWN_FINDING
from app.web.locale import RequestLocale
from app.web.protection import check_limit, limiter

router = APIRouter(prefix="/admin", include_in_schema=False)

Edit = Annotated[
    AdminContext,
    Depends(
        RequirePermission(
            Permission.GROUP_EDIT, group_param="slug", fresh=True, maintenance_safe=True
        )
    ),
]


@dataclass(frozen=True)
class FindingRow:
    """One finding as the page shows it."""

    status: str
    tone: str
    text: str


@dataclass(frozen=True)
class AreaRows:
    """The findings of one part of the chain."""

    label: str
    rows: list[FindingRow]


@dataclass(frozen=True)
class CheckView:
    """The latest check: when, overall status and the findings per area."""

    at: str
    status: str
    tone: str
    areas: list[AreaRows]


def check_service(request: Request) -> IntegrationCheckService:
    """Return the application's integration check service."""
    service: IntegrationCheckService = request.app.state.integration_check_service
    return service


def _view(request: Request, locale: RequestLocale, check: GroupCheck) -> CheckView:
    """Translate a stored check for the page."""
    _ = locale.gettext
    areas: dict[str, list[FindingRow]] = {}
    for finding in check.findings:
        status_key, tone = STATUS_LABELS[finding.status]
        text = _finding_text(finding, _)
        areas.setdefault(_(AREA_LABELS[finding.area]), []).append(
            FindingRow(_(status_key), tone, text)
        )
    status_key, tone = STATUS_LABELS[check.overall]
    return CheckView(
        at=admin_moment(request, locale, check.checked_at),
        status=_(status_key),
        tone=tone,
        areas=[AreaRows(label, rows) for label, rows in areas.items()],
    )


def _finding_text(finding: Finding, _: BoundGettext) -> str:
    """Return the translated sentence of a finding; delivery outcomes are translated too."""
    params = dict(finding.params)
    if "outcome" in params:
        try:
            params["outcome"] = _(OUTCOME_LABELS[Outcome(params["outcome"])])
        except ValueError:
            pass  # an outcome code from a newer version: shown as it is
    key = FINDING_LABELS.get(finding.code)
    if key:
        try:
            return _(key, **params)
        except KeyError:
            pass  # stored by another version without a value the text needs
    return _(UNKNOWN_FINDING, code=finding.code)


async def check_view(
    request: Request, locale: RequestLocale, context: AdminContext
) -> CheckView | None:
    """Return the latest check of the context's group for its settings page, if any."""
    check = await check_service(request).last(context.principal, context.group.group.slug)
    return _view(request, locale, check) if check else None


@router.post("/groups/{slug}/check")
@limiter.limit(check_limit)
async def run_check(request: Request, slug: str, _: SameOrigin, context: Edit) -> Response:
    """Check the group's Webex, WxCC and webhook configuration now."""
    try:
        await check_service(request).run(context.principal, slug)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(f"/admin/groups/{slug}/settings", "checked", anchor="check")
