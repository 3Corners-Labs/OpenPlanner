"""The *Usage reporting* part of System status (ADR 0117 point 4).

Shows how far the rollup of usage facts is: when it last ran and how many facts wait. The worker
rolls facts up every ``reporting_rollup_interval_minutes``; waiting facts and a last run longer
ago than four intervals mean the reports are behind, and the part warns. Only counts and times
are shown, never a fact.

    usage_view(...)  the whole part, for the System status tab
"""

from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from app.adapters.db.uow import SqlUnitOfWork
from app.admin.pages import admin_moment
from app.config import Settings
from app.domain.usage_rollup import LAG_FACTOR, is_lagging
from app.web.locale import RequestLocale


@dataclass(frozen=True)
class UsageView:
    """The *Usage reporting* part as the template shows it.

    Attributes:
        enabled: Whether facts are written (``reporting_enabled``).
        pending: How many facts wait for the rollup.
        last_run: When the rollup last finished, or empty if it never has.
        warning: The warning text, empty when the rollup keeps up.
    """

    enabled: bool
    pending: int
    last_run: str
    warning: str


async def usage_view(request: Request, locale: RequestLocale, settings: Settings) -> UsageView:
    """Describe the rollup; an unreadable database counts as nothing waiting."""
    _ = locale.gettext
    now = request.app.state.clock.now()
    try:
        async with SqlUnitOfWork(request.app.state.engine).read() as tx:
            state = await tx.usage_state()
    except SQLAlchemyError:
        return UsageView(settings.reporting_enabled, 0, "", _("admin.settings.usage.unreadable"))
    interval = timedelta(minutes=settings.reporting_rollup_interval_minutes)
    last = admin_moment(request, locale, state.rolled_at) if state.rolled_at else ""
    warning = ""
    if is_lagging(state, now, interval):
        warning = _(
            "admin.settings.usage.behind", minutes=int(LAG_FACTOR * interval.total_seconds() // 60)
        )
    return UsageView(settings.reporting_enabled, state.pending, last, warning)
