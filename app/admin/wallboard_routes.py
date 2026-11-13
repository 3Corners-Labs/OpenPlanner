"""The live wallboard: a report family of the *Usage* page for a screen in the team's room.

    GET /admin/groups/{slug}/usage?view=wallboard                       reports.wallboard.view
    GET /admin/organisations/{org}/usage?view=wallboard                 reports.wallboard.view
    …?view=wallboard&layout=kiosk                                       the same, full screen
    GET /admin/groups/{slug}/usage/wallboard/live                       reports.wallboard.view
    GET /admin/organisations/{org}/usage/wallboard/live                 reports.wallboard.view

The page is complete without a script: the tiles, the hour bars and a *Refresh* link. With
``static/live-region.js`` the board asks the ``…/live`` address every
``reporting_wallboard_refresh_seconds`` and swaps the fragment in (ADR 0072 §6, the one live
mechanism); nothing is written on that path. The service reads inside the scope of
``reports.wallboard.view`` and audits a denial like every report read. The kiosk layout has no
menu, but is the same page: it needs the same permission and the same session.
"""

from dataclasses import dataclass
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, Response

from app.admin.authz import AdminContext, RequirePermission, can_function
from app.admin.group_routes import group_page
from app.admin.pages import Locale, organisation_header, page, templates
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.scheduling import GroupSummary
from app.domain.wallboard import STALE_AFTER_POLLS, Wallboard
from app.domain.wallboard_service import WallboardService
from app.i18n.dates import format_day
from app.web.organisation_settings import settings_for
from app.web.security_policy import PageKind
from app.web.templates import FragmentResponse, render

router = APIRouter(prefix="/admin", include_in_schema=False)

BoardView = Annotated[AdminContext, Depends(RequirePermission(Permission.REPORTS_WALLBOARD_VIEW))]
"""``reports.wallboard.view`` held anywhere; the service narrows it to the organisation."""

BoardViewGroup = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.REPORTS_WALLBOARD_VIEW, group_param="slug")),
]
"""``reports.wallboard.view`` on the group of the path."""

WALLBOARD = "wallboard"
"""The ``view`` of the usage page that shows the board."""
KIOSK = "kiosk"
"""The ``layout`` of the full-screen variant: no menu, no report nav."""
BAR_HEIGHT = 100
BAR_WIDTH = 40
BAR_STEP = 56
"""The geometry of the hour strip (SVG user units); the strip scales with the screen."""


@dataclass(frozen=True)
class Bar:
    """One hour of the strip as the SVG draws it.

    Attributes:
        hour: The label, two digits.
        count: The figure, or ``None`` when hidden.
        x: Left edge.
        y: Top edge of the bar.
        height: Height of the bar; zero for a hidden or empty hour.
        planned: Shaded: appointments still to come.
        current: The running hour.
    """

    hour: str
    count: int | None
    x: int
    y: int
    height: int
    planned: bool
    current: bool


def wallboard_service(request: Request) -> WallboardService:
    """Return the application's wallboard service."""
    service: WallboardService = request.app.state.wallboard_service
    return service


def bars_of(board: Wallboard) -> list[Bar]:
    """Return the hour strip of ``board`` with its geometry; the tallest shown hour fills it."""
    top = max((b.count or 0 for b in board.bars), default=0) or 1
    bars = []
    for index, bar in enumerate(board.bars):
        height = round(BAR_HEIGHT * (bar.count or 0) / top)
        bars.append(
            Bar(
                f"{bar.hour:02d}",
                bar.count,
                index * BAR_STEP + 12,
                BAR_HEIGHT - height + 14,
                height,
                bar.planned,
                bar.current,
            )
        )
    return bars


def wait_text(seconds: int) -> str:
    """Return a wait as ``m:ss`` (``h:mm:ss`` from an hour), the way a clock reads."""
    minutes, rest = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes}:{rest:02d}"


def board_context(
    request: Request,
    locale: Locale,
    board: Wallboard,
    covered: list[GroupSummary],
    base: str,
    *,
    kiosk: bool = False,
    poll: str | None = None,
    here: str | None = None,
    seconds: int | None = None,
) -> dict[str, object]:
    """Return what the board's templates need: the figures and the words that are not in them.

    Times are the group's own: the first group's zone decides, as it does for the day.
    """
    zone = ZoneInfo(covered[0].group.timezone)
    local = board.as_of.astimezone(zone)
    rolled = board.rolled_at.astimezone(zone).strftime("%H:%M:%S") if board.rolled_at else ""
    live = board.live
    return {
        "board": board,
        "bars": bars_of(board),
        "bar_width": BAR_WIDTH,
        "bar_step": BAR_STEP,
        "bar_height": BAR_HEIGHT,
        "bar_view_width": len(board.bars) * BAR_STEP + 24,
        "as_of_clock": local.strftime("%H:%M:%S"),
        "rolled_clock": rolled,
        "day_text": format_day(board.day, locale.code),
        "wait": wait_text(live.longest_wait_seconds) if live else "",
        "poll": poll or f"{base}/wallboard/live",
        "poll_seconds": seconds or settings_for(request).reporting_wallboard_refresh_seconds,
        "board_groups": [g.group.name for g in covered],
        "board_name": (
            covered[0].group.name if len(covered) == 1 else covered[0].organisation_name
        ),
        "organisation_name": covered[0].organisation_name,
        "kiosk_layout": kiosk,
        "stale_after": STALE_AFTER_POLLS,
        "here": here or f"{base}?view={WALLBOARD}" + (f"&layout={KIOSK}" if kiosk else ""),
    }


async def wallboard_page(
    request: Request,
    locale: Locale,
    principal: Principal,
    base: str,
    views: list[tuple[str, str, str]],
    *,
    organisation: str | None = None,
    group: AdminContext | None = None,
    extra: dict[str, object] | None = None,
) -> HTMLResponse:
    """Render the board, framed in the usage page or, with ``layout=kiosk``, full screen.

    ``extra`` adds to the framed page's context (the display link section of the organisation).

    Raises:
        DomainError: ``not_permitted`` (audited) and ``group_not_found``.
    """
    board, covered = await wallboard_service(request).read(
        principal, organisation, group.group.group.slug if group else None
    )
    kiosk = request.query_params.get("layout") == KIOSK
    ctx = board_context(request, locale, board, covered, base, kiosk=kiosk)
    if kiosk:
        return render(
            templates,
            request,
            "admin/wallboard_kiosk.html",
            locale,
            {**ctx, "exit": f"{base}?view={WALLBOARD}"},
            kind=PageKind.SCRIPTED,
        )
    framed: dict[str, object] = {
        **ctx,
        "views": views,
        "view": WALLBOARD,
        "base": base,
        "kiosk": f"{base}?view={WALLBOARD}&layout={KIOSK}",
        **(extra or {}),
    }
    if group is not None:
        return group_page(
            request, locale, group, "admin/usage_wallboard.html", framed, kind=PageKind.SCRIPTED
        )
    header = organisation_header(
        request, locale, principal, covered[0].organisation_name, organisation or ""
    )
    return page(
        request,
        locale,
        "admin/usage_wallboard.html",
        principal,
        {"org_header": header, "usage_org": organisation or "", **framed},
        kind=PageKind.SCRIPTED,
    )


async def wallboard_fragment(
    request: Request,
    locale: Locale,
    principal: Principal,
    base: str,
    *,
    organisation: str | None = None,
    group_slug: str | None = None,
) -> Response:
    """Return the board's fragment for the poller; no 204: the figures change by the second."""
    board, covered = await wallboard_service(request).read(principal, organisation, group_slug)
    ctx = board_context(request, locale, board, covered, base)
    return render(
        templates,
        request,
        "admin/_wallboard.html",
        locale,
        {**ctx, "can": can_function(principal)},
    )


def wallboard_shown(
    principal: Principal, *, organisation: str | None = None, group: GroupSummary | None = None
) -> bool:
    """Return whether ``principal`` may open the board of the group or organisation."""
    if organisation is not None and group is None:
        # a group-level right is held "anywhere" by a group admin, but the organisation's board
        # adds up its groups: offer it only to a reader whose scope is the whole organisation
        scope = scope_for(principal, Permission.REPORTS_WALLBOARD_VIEW)
        return scope.everything or organisation in scope.organisations
    return can_function(principal)(Permission.REPORTS_WALLBOARD_VIEW, group=group)


@router.get("/groups/{slug}/usage/wallboard/live", response_class=FragmentResponse)
async def group_live(
    request: Request, locale: Locale, slug: str, context: BoardViewGroup
) -> Response:
    """Return a group's board fragment for the poller (``static/live-region.js``)."""
    return await wallboard_fragment(
        request, locale, context.principal, f"/admin/groups/{slug}/usage", group_slug=slug
    )


@router.get("/organisations/{org}/usage/wallboard/live", response_class=FragmentResponse)
async def organisation_live(
    request: Request, locale: Locale, org: str, context: BoardView
) -> Response:
    """Return the board fragment of an organisation's groups for the poller."""
    return await wallboard_fragment(
        request, locale, context.principal, f"/admin/organisations/{org}/usage", organisation=org
    )
