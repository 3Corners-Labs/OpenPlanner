"""When and to what an installation updates itself: line, window, support and stop (ADR 0116).

Four rules on top of the update machinery of ``app.domain.updates``:

- **Line** (point 9): ``update_line`` ``X.Y`` makes the installation follow the patches of one
  minor only; empty follows everything its channel offers.
- **Window** (point 9): ``update_window`` holds up to seven weekly ranges in the
  installation's time zone (``mon 02:00-04:00, sat 01:00-05:00``). The worker turns a newer
  version into a request only inside a range, and the updater claims a request the worker made
  only inside one; a superuser's click is immediate, and may carry ``apply_after``. Empty
  means any time. Auto-apply works in production only with a window (amends ADR 0067 point 1).
- **Support** (point 9): ``support.json`` names every minor's line and its status; the portal
  shows the running line's.
- **Stop** (point 6): ``MIN_UPGRADE_FROM`` in an image refuses a database last run by an older
  version (``upgrade_path_required``); the upgrade goes through the stop first.

Pure Python; times are UTC ``datetime`` values, converted to the installation's zone here.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.audit import SYSTEM_ACTOR

# LineStatus and SupportLine are defined with the release check that carries them; they are
# named here too, next to the rules that read them.
from app.domain.updates import LineStatus as LineStatus
from app.domain.updates import ListedRelease, UpdateRequest, UpdateStatus, Version
from app.domain.updates import SupportLine as SupportLine

MAX_RANGES = 7
"""At most this many ranges in ``update_window``."""
MIN_RANGE = timedelta(minutes=30)
"""A range shorter than this cannot hold an update with its health wait."""
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
"""The weekday names of ``update_window``, ``date.weekday()`` order."""
_LINE = re.compile(r"^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$")
_RANGE = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun)\s+(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})$")
_WEEK = timedelta(days=7)


def valid_line(value: str) -> bool:
    """Whether ``value`` is a valid ``update_line``: empty, or ``X.Y``."""
    return value == "" or _LINE.fullmatch(value) is not None


def on_line(listing: Sequence[ListedRelease], line: str) -> list[ListedRelease]:
    """Return the listed releases of line ``X.Y``; all of them when ``line`` is empty."""
    if not line:
        return list(listing)
    return [item for item in listing if item.version.line == line]


@dataclass(frozen=True)
class WindowRange:
    """One weekly range of ``update_window``: a weekday and a start and end on that day.

    Attributes:
        weekday: ``date.weekday()`` (0 is Monday).
        start: The local time the range opens.
        end: The local time it closes, after ``start`` on the same day.
    """

    weekday: int
    start: time
    end: time


@dataclass(frozen=True)
class UpdateWindow:
    """When automatic updates may happen, in the installation's time zone (ADR 0116 point 9).

    Attributes:
        ranges: The weekly ranges; empty means any time.
        zone: The installation's time zone (``admin_timezone``).
    """

    ranges: tuple[WindowRange, ...]
    zone: ZoneInfo

    @property
    def any_time(self) -> bool:
        """Whether there is no window: automatic updates at any time."""
        return not self.ranges

    def is_open(self, now: datetime) -> bool:
        """Whether ``now`` (UTC) lies inside a range."""
        if self.any_time:
            return True
        local = now.astimezone(self.zone)
        return any(
            local.weekday() == r.weekday and r.start <= local.time() < r.end for r in self.ranges
        )

    def next_opening(self, now: datetime) -> datetime | None:
        """Return when the window is next open from ``now`` on (UTC); ``now`` when it is open.

        ``None`` without a window: it is never closed.
        """
        if self.any_time:
            return None
        if self.is_open(now):
            return now
        local = now.astimezone(self.zone)
        openings = []
        for r in self.ranges:
            day = local.date() + timedelta(days=(r.weekday - local.weekday()) % 7)
            opening = datetime.combine(day, r.start, tzinfo=self.zone)
            if opening <= local:
                opening += _WEEK
            openings.append(opening)
        return min(openings).astimezone(now.tzinfo)


ANY_TIME = UpdateWindow((), ZoneInfo("UTC"))
"""No window: automatic updates at any time (the default of ``update_window``)."""


def parse_window(value: str, zone: ZoneInfo) -> UpdateWindow:
    """Read ``update_window``: ranges ``ddd hh:mm-hh:mm`` separated by commas.

    Raises:
        ValueError: A range is malformed, ends before it starts, is shorter than ``MIN_RANGE``,
            or there are more than ``MAX_RANGES``.
    """
    parts = [part.strip().lower() for part in value.split(",") if part.strip()]
    if len(parts) > MAX_RANGES:
        raise ValueError(f"at most {MAX_RANGES} ranges")
    ranges = []
    for part in parts:
        found = _RANGE.fullmatch(part)
        if found is None:
            raise ValueError(f"{part!r} is not a range like 'mon 02:00-04:00'")
        day, sh, sm, eh, em = found.groups()
        try:
            start, end = time(int(sh), int(sm)), time(int(eh), int(em))
        except ValueError as error:
            raise ValueError(f"{part!r} has no valid time") from error
        length = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
        if length < MIN_RANGE.total_seconds() / 60:
            raise ValueError(f"{part!r} is shorter than {int(MIN_RANGE.total_seconds() // 60)} min")
        ranges.append(WindowRange(WEEKDAYS.index(day), start, end))
    return UpdateWindow(tuple(ranges), zone)


def valid_window(value: str) -> bool:
    """Whether ``value`` is a valid ``update_window`` (the zone does not matter for that)."""
    try:
        parse_window(value, ZoneInfo("UTC"))
    except ValueError:
        return False
    return True


def auto_apply_effective(auto_apply: bool, window: UpdateWindow, production: bool) -> bool:
    """Whether the worker requests updates itself: in production only with a window set."""
    return auto_apply and (not production or not window.any_time)


def claimable(
    request: UpdateRequest, now: datetime, window: UpdateWindow, production: bool = False
) -> bool:
    """Whether the updater may take ``request`` now.

    A request waits until its ``apply_after``; one the worker made itself waits for the
    window as well, and in production is never taken without a window (the window was
    cleared after the worker asked: auto-apply no longer applies there). A superuser's
    request without ``apply_after`` is taken at once.
    """
    if request.status is not UpdateStatus.REQUESTED:
        return False
    if request.apply_after is not None and now < request.apply_after:
        return False
    if request.requested_by != SYSTEM_ACTOR:
        return True
    return window.is_open(now) and not (production and window.any_time)


def parse_support(text: str) -> list[SupportLine]:
    """Read ``support.json``; lines with an unknown status or a malformed entry are skipped.

    Raises:
        ValueError: The text is no JSON object with a ``lines`` list.
    """
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        raise ValueError("support.json needs a 'lines' list")
    found = []
    for entry in data["lines"]:
        if not isinstance(entry, Mapping):
            continue
        line, status, until = entry.get("line"), entry.get("status"), entry.get("until")
        if not isinstance(line, str) or not _LINE.fullmatch(line):
            continue
        if status not in {s.value for s in LineStatus}:
            continue
        found.append(
            SupportLine(line, LineStatus(status), until if isinstance(until, str) else None)
        )
    return found


def line_support(lines: Sequence[SupportLine], version: Version | None) -> SupportLine | None:
    """Return the entry of the line ``version`` runs on; a line not listed has ended.

    ``None`` for a build that is no release, or when no ``support.json`` was read.
    """
    if version is None or not lines:
        return None
    return next(
        (entry for entry in lines if entry.line == version.line),
        SupportLine(version.line, LineStatus.ENDED),
    )


def upgrade_stop(
    image: Version | None, last_run: Version | None, stop: Version | None
) -> Version | None:
    """Return the stop an upgrade must go through first, or ``None`` when it may go ahead.

    Args:
        image: ``APP_VERSION`` of the image about to migrate.
        last_run: The version that last ran on this database (``running_versions``).
        stop: ``MIN_UPGRADE_FROM`` baked into the image.

    A stop applies only to images above it: the stop itself is where older databases go
    first (1.0.0 takes every 0.x database). Unknown versions (a new database, a build that is
    no release) know no stop.
    """
    if image is None or last_run is None or stop is None or image <= stop:
        return None
    return stop if last_run < stop else None
