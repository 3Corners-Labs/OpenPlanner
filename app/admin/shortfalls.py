"""The shortfall notice on group pages and the dashboard (ADR 0044).

A slot with more confirmed bookings than agents is a shortfall (``app.domain.agenda``). The
group's overview and its staffing and exception pages show how many slots ahead are short,
linking to the first one in the agenda, so a staffing change that causes one is seen at once.
Shown only to admins who may view the group's bookings; the text never relies on colour.
"""

from dataclasses import dataclass

from fastapi import Request

from app.admin.authz import AdminContext, can_function
from app.domain.agenda_service import AgendaService, ShortfallSummary
from app.domain.authorization import Permission
from app.domain.scheduling import Group
from app.i18n.dates import format_clock, format_day
from app.web.locale import RequestLocale


@dataclass(frozen=True)
class ShortfallView:
    """The notice: the count, the horizon and a link to the first shortfall."""

    count: int
    days: int
    first_label: str
    first_href: str


def agenda_service(request: Request) -> AgendaService:
    """Return the application's agenda service."""
    service: AgendaService = request.app.state.agenda_service
    return service


def shortfall_view(summary: ShortfallSummary, group: Group, locale: RequestLocale) -> ShortfallView:
    """Turn a summary into the notice of ``group``."""
    first = summary.first
    if first is None:
        return ShortfallView(0, summary.horizon_days, "", "")
    tz, slug = group.tz, group.slug
    day = first.start.astimezone(tz).date()
    label = f"{format_day(day, locale.code)}, {format_clock(first.start, tz, locale.code)}"
    href = f"/admin/groups/{slug}/agenda?day={day.isoformat()}#slot-{first.start:%Y%m%dT%H%M}"
    return ShortfallView(summary.count, summary.horizon_days, label, href)


async def group_shortfalls(
    request: Request, locale: RequestLocale, context: AdminContext
) -> ShortfallView | None:
    """Return the notice of the context's group, or ``None`` without ``bookings.view``."""
    summary = context.group
    if not can_function(context.principal)(Permission.BOOKINGS_VIEW, summary):
        return None
    group = summary.group
    found = await agenda_service(request).shortfalls(context.principal, group.slug)
    return shortfall_view(found, group, locale)
