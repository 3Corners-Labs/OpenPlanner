"""Made-up usage cells for the demo group, so the report pages have something to draw (ADR 0117).

Only the end-to-end run and a developer's demo use this (``seed-closures`` refuses to run in
production): a week and a day of hourly cells for the screens *Queue and calls*, *Capacity* and
*Channels*, written as the rollup would write them. Counts are chosen to be above the small-cell
threshold on most days and below it on one, so the pages show both a drawn value and a gap.
"""

from datetime import datetime, timedelta
from functools import partial
from typing import Any

from app.domain.booking import BookingKind
from app.domain.channels import ChannelKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import ActorClass, UsageCell, duration_bucket, hour_of

EXAMPLE_DAYS = 9
"""How many days back the example cells reach."""


def _cell(
    hour: datetime,
    organisation_id: int,
    group_id: int,
    metric: str,
    count: int,
    total: int = 0,
    **cuts: Any,
) -> UsageCell:
    """Return one example cell; ``total`` is both its sum and its maximum."""
    return UsageCell(hour, organisation_id, group_id, metric, count, total, total, **cuts)


def usage_example_cells(organisation_id: int, group_id: int, now: datetime) -> list[UsageCell]:
    """Return example cells for one group: a morning hour on each of the last ``EXAMPLE_DAYS`` days.

    The second day back holds few calls on purpose, so the charts show a hidden day.
    """
    cells: list[UsageCell] = []
    for back in range(1, EXAMPLE_DAYS + 1):
        hour = hour_of(now - timedelta(days=back)).replace(hour=8)
        busy = 3 if back == 2 else 24 + back
        cell = partial(_cell, hour, organisation_id, group_id)
        cells += [
            cell(
                "Q1",
                busy,
                booking_kind=BookingKind.SCHEDULED,
                dispatch_kind=DispatchKind.CONTACT_CENTER,
            ),
            cell(
                "Q1",
                busy // 2,
                booking_kind=BookingKind.DIRECT,
                dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
            ),
            cell("Q2", busy, 90 * busy, bucket=duration_bucket(90)),
            cell("Q2", busy // 2, 400 * (busy // 2), bucket=duration_bucket(400)),
            cell("Q5", busy, 900 * busy, bucket=duration_bucket(900)),
            cell(
                "Q9",
                busy,
                3,
            ),
            cell("C1", busy, 3 * busy, reason_code="booked"),
            cell("C1", busy, 4 * busy, reason_code="capacity"),
            cell("C3", busy, busy),
            cell("C2", busy // 2, reason_code="full"),
            cell("C2", busy // 2, reason_code="under_full"),
            cell("C4", busy),
            cell("M1", busy, channel_kind=ChannelKind.EMAIL, reason_code="reminder.sent"),
            cell("M1", busy // 4, channel_kind=ChannelKind.EMAIL, reason_code="reminder.failed"),
            cell("M2", busy, locale="nl"),
            cell("M2", busy // 3, locale="en"),
            cell("T1", busy, reason_code="created"),
            cell("T1", busy // 2, reason_code="joined"),
            cell("U8", busy // 3, reason_code="proposed", actor_class=ActorClass.PLANNER),
        ]
    return cells
