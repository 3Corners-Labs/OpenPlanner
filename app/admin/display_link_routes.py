"""The manager side of the display links: make, list, revoke (ADR 0117 point 20, step 14).

Both need ``reports.wallboard.link.manage``:

    POST /admin/organisations/{org}/usage/wallboard/links
    POST /admin/organisations/{org}/usage/wallboard/links/{id}/revoke

The list and the form are a section of the organisation's wallboard page (``view=wallboard``,
Usage), next to the board they open; ``links_context`` gives the page what it needs. A link is
made by a POST whose answer *is* the page that shows it, once: the token is in that one response
(``Cache-Control: no-store``) and nowhere else, not in a redirect, a log line or the audit event.
A refused form goes back to the board's page with a code (``?refused=``) like every refused action.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, admin_service, can_function
from app.admin.forms import (
    raise_refusal,
    read_form,
    redirect_done,
    redirect_refused,
    refused_message,
)
from app.admin.pages import Locale, SameOrigin, admin_moment, organisation_header, page
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.wallboard_link import LinkStatus, WallboardLink, status_of
from app.domain.wallboard_link_service import WallboardLinkService
from app.web.organisation_settings import settings_for

router = APIRouter(prefix="/admin", include_in_schema=False)

ManageLinks = Annotated[
    AdminContext, Depends(RequirePermission(Permission.REPORTS_WALLBOARD_LINK_MANAGE))
]
"""``reports.wallboard.link.manage`` held anywhere; the service narrows it to the organisation."""

ERRORS: dict[str, str] = {
    "enabled": "reports.display.error.enabled",
    "name": "reports.display.error.name",
    "days": "reports.display.error.days",
    "networks": "reports.display.error.networks",
    "group": "reports.display.error.group",
}
"""Why a link was refused, by the field of the ``DomainError`` (never its value)."""
STATUS_LABELS: dict[str, str] = {
    LinkStatus.NOT_OPENED.value: "reports.display.status.not_opened",
    LinkStatus.ACTIVE.value: "reports.display.status.active",
    LinkStatus.EXPIRED.value: "reports.display.status.expired",
    LinkStatus.REVOKED.value: "reports.display.status.revoked",
}
LIFETIMES = (1, 7, 30, 90)
"""The lives a link offers in days, cut at the platform's longest; that longest is offered too."""


def link_service(request: Request) -> WallboardLinkService:
    """Return the application's display link service."""
    service: WallboardLinkService = request.app.state.wallboard_link_service
    return service


@dataclass(frozen=True)
class LinkRow:
    """One link as the list shows it.

    Attributes:
        id: For the revoke form.
        name: The screen's name.
        scope: The group's name, or ``None`` for all groups of the organisation.
        status: Catalogue key of the status.
        tone: The status as a chip tone.
        pinned: The pinned range, or ``None`` while it is not pinned (yet).
        pin: Whether the link pins at all.
        networks: The ACL; empty means any network.
        last_used: When the screen last asked, or ``None`` for never.
        expires: When the link ends.
        can_revoke: Whether *Revoke* is offered.
    """

    id: int
    name: str
    scope: str | None
    status: str
    tone: str
    pinned: str | None
    pin: bool
    networks: tuple[str, ...]
    last_used: str | None
    expires: str
    can_revoke: bool


def _row(
    request: Request, locale: Locale, link: WallboardLink, group_name: dict[str, str]
) -> LinkRow:
    """Return the list row of ``link``: statuses in words, moments in the admin's zone."""
    status = status_of(link, request.app.state.clock.now())
    tone = {
        LinkStatus.ACTIVE: "success",
        LinkStatus.NOT_OPENED: "warning",
        LinkStatus.EXPIRED: "",
        LinkStatus.REVOKED: "danger",
    }[status]
    return LinkRow(
        link.id or 0,
        link.name,
        group_name.get(link.group_slug or "") if link.group_slug else None,
        STATUS_LABELS[status.value],
        tone,
        link.pinned_network,
        link.pin,
        link.networks,
        admin_moment(request, locale, link.last_used_at) if link.last_used_at else None,
        admin_moment(request, locale, link.expires_at),
        status in (LinkStatus.ACTIVE, LinkStatus.NOT_OPENED),
    )


async def links_context(
    request: Request, locale: Locale, principal: Principal, org: str
) -> dict[str, object]:
    """Return what the display link section of the organisation's wallboard page shows.

    Empty for who does not hold ``reports.wallboard.link.manage`` on the organisation, so the
    page for a reader is the page it was.
    """
    if not can_function(principal)(Permission.REPORTS_WALLBOARD_LINK_MANAGE, organisation=org):
        return {}
    settings = settings_for(request)
    found = await admin_service(request).groups(principal, Permission.REPORTS_WALLBOARD_VIEW)
    groups = [(g.group.slug, g.group.name) for g in found if g.organisation_slug == org]
    rows = await link_service(request).rows(principal, org)
    longest = settings.reporting_wallboard_link_max_days
    lives = sorted({d for d in LIFETIMES if d <= longest} | {longest})
    return {
        "links": {
            "rows": [_row(request, locale, r, dict(groups)) for r in rows],
            "groups": groups,
            "enabled": settings.reporting_wallboard_display_links_enabled,
            "lives": lives,
            "longest": longest,
            "default_networks": settings.reporting_wallboard_link_networks,
            "pin": settings.reporting_wallboard_link_pin_default,
            "error": refused_message(request, ERRORS, locale.gettext),
        }
    }


@router.post("/organisations/{org}/usage/wallboard/links", response_model=None)
async def link_create(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: ManageLinks
) -> Response:
    """Make a display link and show it, once, on the page that answers this request."""
    first = await read_form(request, locale)
    days = first.values.get("days", "")
    back = f"/admin/organisations/{org}/usage?view=wallboard"
    try:
        created = await link_service(request).create(
            context.principal,
            org,
            group_slug=first.values.get("group") or None,
            name=first.values.get("name", ""),
            days=int(days) if days.isascii() and days.isdigit() and len(days) <= 3 else 0,
            networks=first.values.get("networks", "")[:2000],
            pin=first.values.get("pin") == "on",
        )
    except DomainError as error:
        raise_refusal(error)
        field = "group" if error.code is ErrorCode.GROUP_NOT_FOUND else (error.field or "name")
        return redirect_refused(back, field if field in ERRORS else "name")
    base = request.app.state.settings.public_base_url.rstrip("/")
    header = organisation_header(
        request, locale, context.principal, created.link.organisation_slug, org
    )
    response: HTMLResponse = page(
        request,
        locale,
        "admin/wallboard_link_created.html",
        context.principal,
        {
            "org_header": header,
            "link_url": f"{base}/display/start/{created.token}",
            "link_name": created.link.name,
            "link_expires": admin_moment(request, locale, created.link.expires_at),
            "back": back,
        },
    )
    response.headers["cache-control"] = "no-store"
    response.headers["referrer-policy"] = "no-referrer"
    return response


@router.post("/organisations/{org}/usage/wallboard/links/{link_id}/revoke", response_model=None)
async def link_revoke(
    request: Request, org: str, link_id: int, _: SameOrigin, context: ManageLinks
) -> Response:
    """Revoke a link of the organisation; its screen stops within one poll."""
    back = f"/admin/organisations/{org}/usage?view=wallboard"
    try:
        await link_service(request).revoke(context.principal, org, link_id)
    except DomainError as error:
        raise_refusal(error)
        return redirect_done(back, "unchanged", anchor="links")
    return redirect_done(back, "link_revoked", anchor="links")
