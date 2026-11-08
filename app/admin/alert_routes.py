"""Admin routes for alert targets, the test alert and the Webex bot token (ADR 0082 point 9).

Two homes, one per scope (ADR 0047): the platform's targets on *Koppelingen*
(``settings.platform.manage``), an organisation's on its *Kanalen* page
(``integrations.manage`` on it). Both render the same card (``alert_view``) and post to the same
handlers. ``AlertTargetService`` authorises again, validates and audits; a target's address and
the bot token are never written to the audit log, a log line or a URL.

    POST /admin/integrations/alerts/targets                          add a platform target
    POST /admin/integrations/alerts/targets/{target_id}/remove       remove one
    POST /admin/integrations/alerts/targets/{target_id}/test         send a test alert
    POST /admin/integrations/alerts/bot                              set or clear the bot token
    POST /admin/organisations/{org}/channels/alerts/...               the same for an organisation
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import raise_refusal, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin
from app.domain.alert_delivery import AlertTargetService
from app.domain.alert_model import TARGET_LEVELS, AlertChannelKind, AlertTarget
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError, ErrorCode
from app.i18n import BoundGettext

router = APIRouter(prefix="/admin", include_in_schema=False)

Platform = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.SETTINGS_PLATFORM_MANAGE, fresh=True)),
]
"""The platform's alert targets and bot (ADR 0082 *Settings*)."""
Organisation = Annotated[
    AdminContext,
    Depends(
        RequirePermission(Permission.INTEGRATIONS_MANAGE, organisation_param="org", fresh=True)
    ),
]
"""An organisation's alert targets and bot."""

KIND_LABELS: dict[AlertChannelKind, str] = {
    AlertChannelKind.EMAIL: "admin.alerts.target.kind.email",
    AlertChannelKind.WEBEX_SPACE: "admin.alerts.target.kind.webex_space",
}
LEVEL_LABELS: dict[str, str] = {
    "warning": "admin.alerts.target.level.warning",
    "outage": "admin.alerts.target.level.outage",
}


def alert_service(request: Request) -> AlertTargetService:
    """Return the application's alert target service."""
    service: AlertTargetService = request.app.state.alert_target_service
    return service


def _page(organisation: str | None) -> str:
    """Return the page that shows the card of the scope."""
    return (
        f"/admin/organisations/{organisation}/channels" if organisation else "/admin/integrations"
    )


def _action(organisation: str | None) -> str:
    """Return the base path the card's forms post to."""
    if organisation:
        return f"/admin/organisations/{organisation}/channels/alerts"
    return "/admin/integrations/alerts"


async def alert_view(
    request: Request, gettext: BoundGettext, principal: Principal, organisation: str | None
) -> dict[str, object]:
    """Return the alert card of the scope: its targets, whether a bot is set, the form choices.

    The address is shown to who may manage it: it is an administrator's own address or a room
    id, never a participant's.
    """
    service = alert_service(request)
    targets = await service.targets(principal, organisation)
    return {
        "action": _action(organisation),
        "targets": [
            {
                "id": t.id,
                "kind": gettext(KIND_LABELS[t.kind]),
                "address": t.address,
                "level": gettext(LEVEL_LABELS[t.min_level]),
            }
            for t in targets
        ],
        "has_bot": await service.has_bot(principal, organisation),
        "kinds": [(k.value, gettext(KIND_LABELS[k])) for k in AlertChannelKind],
        "levels": [(level, gettext(LEVEL_LABELS[level])) for level in TARGET_LEVELS],
    }


async def _add(
    request: Request, locale: Locale, principal: Principal, organisation: str | None
) -> Response:
    """Add a target from the posted form; a refusal returns to the page with a message."""
    form = await read_form(request, locale)
    kind, level = form.text("kind"), form.text("min_level")
    try:
        if kind not in {k.value for k in AlertChannelKind}:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "kind")
        target = AlertTarget(None, None, AlertChannelKind(kind), form.text("address"), level)
        await alert_service(request).add(principal, organisation, target)
    except DomainError as error:
        raise_refusal(error)
        return redirect_done(_page(organisation), "alert_refused", anchor="alerts")
    return redirect_done(_page(organisation), "alert_target_added", anchor="alerts")


async def _remove(
    request: Request, principal: Principal, organisation: str | None, target_id: int
) -> Response:
    """Remove one target."""
    try:
        await alert_service(request).remove(principal, organisation, target_id)
    except DomainError as error:
        raise_refusal(error)
        return redirect_done(_page(organisation), "alert_refused", anchor="alerts")
    return redirect_done(_page(organisation), "alert_target_removed", anchor="alerts")


async def _test(
    request: Request, principal: Principal, organisation: str | None, target_id: int
) -> Response:
    """Send a test alert to one target and say whether it went out."""
    try:
        error = await alert_service(request).send_test(principal, organisation, target_id)
    except DomainError as refused:
        raise_refusal(refused)
        return redirect_done(_page(organisation), "alert_refused", anchor="alerts")
    done = "alert_test_sent" if error is None else "alert_test_failed"
    return redirect_done(_page(organisation), done, anchor="alerts")


async def _bot(
    request: Request, locale: Locale, principal: Principal, organisation: str | None
) -> Response:
    """Store or remove the bot token; the token is never shown again."""
    form = await read_form(request, locale)
    token = None if form.checked("remove") else form.text("token") or None
    try:
        await alert_service(request).set_bot(principal, organisation, token)
    except DomainError as error:
        raise_refusal(error)
        return redirect_done(_page(organisation), "alert_refused", anchor="alerts")
    return redirect_done(
        _page(organisation), "alert_bot_saved" if token else "alert_bot_removed", anchor="alerts"
    )


@router.post("/integrations/alerts/targets")
async def add_platform_target(
    request: Request, locale: Locale, _: SameOrigin, context: Platform
) -> Response:
    """Add an alert target of the platform."""
    return await _add(request, locale, context.principal, None)


@router.post("/integrations/alerts/targets/{target_id}/remove")
async def remove_platform_target(
    request: Request, target_id: int, _: SameOrigin, context: Platform
) -> Response:
    """Remove an alert target of the platform."""
    return await _remove(request, context.principal, None, target_id)


@router.post("/integrations/alerts/targets/{target_id}/test")
async def send_platform_test(
    request: Request, target_id: int, _: SameOrigin, context: Platform
) -> Response:
    """Send a test alert to a target of the platform."""
    return await _test(request, context.principal, None, target_id)


@router.post("/integrations/alerts/bot")
async def platform_bot(
    request: Request, locale: Locale, _: SameOrigin, context: Platform
) -> Response:
    """Set or clear the platform's bot token."""
    return await _bot(request, locale, context.principal, None)


@router.post("/organisations/{org}/channels/alerts/targets")
async def add_organisation_target(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Organisation
) -> Response:
    """Add an alert target of an organisation."""
    return await _add(request, locale, context.principal, org)


@router.post("/organisations/{org}/channels/alerts/targets/{target_id}/remove")
async def remove_organisation_target(
    request: Request, org: str, target_id: int, _: SameOrigin, context: Organisation
) -> Response:
    """Remove an alert target of an organisation."""
    return await _remove(request, context.principal, org, target_id)


@router.post("/organisations/{org}/channels/alerts/targets/{target_id}/test")
async def send_organisation_test(
    request: Request, org: str, target_id: int, _: SameOrigin, context: Organisation
) -> Response:
    """Send a test alert to a target of an organisation."""
    return await _test(request, context.principal, org, target_id)


@router.post("/organisations/{org}/channels/alerts/bot")
async def organisation_bot(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Organisation
) -> Response:
    """Set or clear an organisation's bot token."""
    return await _bot(request, locale, context.principal, org)
