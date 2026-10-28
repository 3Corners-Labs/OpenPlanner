"""Closure wording shared by citizen and admin pages: the banner as people read it (ADR 0024)."""

from dataclasses import dataclass
from datetime import datetime

from app.domain.closure import Closure
from app.domain.scheduling import Group
from app.i18n.dates import format_clock, format_day, local_day
from app.web.locale import RequestLocale


@dataclass(frozen=True)
class ClosureView:
    """A closure ready for a template.

    Attributes:
        title: ``Tijdelijk gesloten`` while active, a warning while upcoming.
        period: From when until when, in the group's time zone.
        paragraphs: The notice in the reader's language, split into paragraphs.
        active: Whether the group is closed right now.
        heading: The admin's heading in the reader's language (ADR 0037), if any.
    """

    title: str
    period: str
    paragraphs: list[str]
    active: bool
    heading: str | None = None


def moment_label(moment: datetime, group: Group, locale: str) -> str:
    """Return e.g. ``maandag 5 januari 2026, 09:00`` in the group's time zone."""
    day = format_day(local_day(moment, group.tz), locale)
    return f"{day}, {format_clock(moment, group.tz, locale)}"


def closure_view(
    closure: Closure, group: Group, locale: RequestLocale, now: datetime
) -> ClosureView:
    """Return the banner of ``closure`` for ``locale`` at ``now``."""
    _ = locale.gettext
    start = moment_label(closure.starts_at, group, locale.code)
    if closure.ends_at is None:
        period = _("closure.banner.period_open", start=start)
    else:
        period = _(
            "closure.banner.period",
            start=start,
            end=moment_label(closure.ends_at, group, locale.code),
        )
    active = closure.is_active(now)
    title = _("closure.banner.title_active") if active else _("closure.banner.title_upcoming")
    paragraphs = [p for p in closure.notice(locale.code).split("\n") if p.strip()]
    return ClosureView(title, period, paragraphs, active, closure.heading(locale.code))
