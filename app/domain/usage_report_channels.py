"""The *Channels* report: messages sent, the language mix and trial calls (ADR 0117 step 6).

A pure function from the hourly cells of the groups in scope to the tables of metrics M1 (messages
per channel and purpose), M2 (bookings per locale) and T1 (trial calls). It holds no database and
no text. Every count goes through the small-cell rules of ``usage_suppress``.

    cells ─► purpose and state of a message / locale of a booking / step of a trial ─► ``Channels``

The locale cut is the usual way to a person ("the only English-speaking booking on Tuesday"), so
it is offered per group only when the group's own total in the range is at least five times the
threshold (ADR 0117 point 8); a group below that is left out of the cut and counted. With no cells
every table is empty and says so, never an error.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from app.domain.channels import ChannelKind
from app.domain.usage import UsageCell
from app.domain.usage_report import (
    CountTable,
    GridTable,
    ReportGroup,
    count_table,
    grid_table,
    local_cells,
    zones_of,
)

PURPOSES: tuple[str, ...] = ("confirmation", "reminder", "change", "cancellation")
"""The purposes that always have a row in the message table (metric M1)."""
SENT = "sent"
FAILED = "failed"
STATES = (SENT, FAILED)
"""The states a message ends in, the second half of a message cell's ``reason_code``."""
TRIAL_STEPS: tuple[str, ...] = ("created", "joined", "entered", "failed")
"""The steps of a trial call, in order (metric T1)."""
METRICS: frozenset[str] = frozenset({"M1", "M2", "T1"})
"""The metrics of this screen: a cell of any other metric is none of its business."""
LOCALE_GROUP_FACTOR = 5
"""A group is cut by locale only when its total in the range is this many times the threshold."""


@dataclass(frozen=True)
class MessageTable:
    """Messages sent and failed, by purpose and by channel (M1).

    Each grid has the two states (sent, failed) as columns and is suppressed along both axes, so a
    hidden cell cannot be recovered from a row or column total.

    Attributes:
        by_purpose: One row per purpose.
        by_channel: One row per channel.
        failure_percent: Failed over sent plus failed in whole percent, ``None`` where hidden.
    """

    by_purpose: GridTable
    by_channel: GridTable
    failure_percent: int | None


@dataclass(frozen=True)
class ChannelsReport:
    """Everything the *Channels* screen shows, with small cells already hidden.

    Attributes:
        first_day: The first day of the range (group-local).
        last_day: The last day of the range.
        as_of: When the rollup last ran.
        threshold: The small-cell threshold that was applied.
        empty: Whether the range holds no cell of this screen's metrics at all; the page says
            so instead of a table of zeros.
        messages: Messages per purpose and channel (M1).
        locales: Bookings per locale (M2).
        locales_groups_left_out: Groups too small to be cut by locale.
        trial: Trial calls per step (T1).
        trial_joined_percent: Trials followed over trials made, in whole percent.
        trial_entered_percent: Trials entered over trials followed, in whole percent.
    """

    first_day: date
    last_day: date
    as_of: datetime | None
    threshold: int
    empty: bool
    messages: MessageTable
    locales: CountTable
    locales_groups_left_out: int
    trial: CountTable
    trial_joined_percent: int | None
    trial_entered_percent: int | None


def _percent(part: int | None, whole: int | None) -> int | None:
    """Return ``part`` over ``whole`` in whole percent, or ``None`` if either is hidden."""
    if part is None or whole is None or whole <= 0:
        return None
    return round(100 * part / whole)


def _messages(
    counts: Counter[tuple[str, str, str]], purposes: Sequence[str], threshold: int
) -> MessageTable:
    """Return the message tables: sent and failed per purpose and per channel, suppressed."""
    channels = [k.value for k in ChannelKind]
    by_purpose: Counter[tuple[str, str]] = Counter()
    by_channel: Counter[tuple[str, str]] = Counter()
    for (purpose, channel, state), count in counts.items():
        by_purpose[(purpose, state)] += count
        by_channel[(channel, state)] += count
    purpose_grid = grid_table(by_purpose, purposes, STATES, threshold)
    return MessageTable(
        purpose_grid,
        grid_table(by_channel, channels, STATES, threshold),
        _percent(
            purpose_grid.column_totals[1],
            _sum(purpose_grid.column_totals[0], purpose_grid.column_totals[1]),
        ),
    )


def _sum(a: int | None, b: int | None) -> int | None:
    """Return ``a + b``, or ``None`` if either is hidden."""
    return None if a is None or b is None else a + b


def channels_report(
    cells: Sequence[UsageCell],
    groups: Sequence[ReportGroup],
    first_day: date,
    last_day: date,
    *,
    threshold: int,
    as_of: datetime | None,
) -> ChannelsReport:
    """Build the *Channels* tables from ``cells`` of ``groups`` over the range.

    Only cells of the given groups count, and only those whose hour falls on a day of the range
    in that group's time zone. ``threshold`` is the effective small-cell threshold.
    """
    messages: Counter[tuple[str, str, str]] = Counter()
    locale_by_group: dict[int, Counter[str]] = {}
    trial: Counter[str] = Counter()
    seen = False
    for cell, _ in local_cells(cells, zones_of(groups), first_day, last_day):
        seen = seen or cell.metric in METRICS
        if cell.metric == "M1" and cell.channel_kind and cell.reason_code:
            purpose, _, state = cell.reason_code.rpartition(".")
            if purpose and state in (SENT, FAILED):
                messages[(purpose, cell.channel_kind.value, state)] += cell.count
        elif cell.metric == "M2" and cell.locale:
            locale_by_group.setdefault(cell.group_id, Counter())[cell.locale] += cell.count
        elif cell.metric == "T1" and cell.reason_code:
            trial[cell.reason_code] += cell.count
    cut = {
        g.group_id
        for g in groups
        if sum(locale_by_group.get(g.group_id, Counter()).values())
        >= LOCALE_GROUP_FACTOR * threshold
    }
    locales: Counter[str] = Counter()
    for group_id in cut:
        locales.update(locale_by_group[group_id])
    purposes = [*PURPOSES, *sorted({p for p, _, _ in messages} - set(PURPOSES))]
    trial_table = count_table(trial, TRIAL_STEPS, threshold)
    created, joined, entered, _failed = (r.count for r in trial_table.rows)
    return ChannelsReport(
        first_day=first_day,
        last_day=last_day,
        as_of=as_of,
        threshold=threshold,
        empty=not seen,
        messages=_messages(messages, purposes, threshold),
        locales=count_table(locales, sorted(locales), threshold),
        locales_groups_left_out=sum(1 for g in groups if g.group_id not in cut),
        trial=trial_table,
        trial_joined_percent=_percent(joined, created),
        trial_entered_percent=_percent(entered, joined),
    )
