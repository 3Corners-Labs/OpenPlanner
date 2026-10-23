"""Group configuration and opening hours.

A ``Group`` is a team (a WxCC queue) citizens book with. Its opening hours come from weekly
``ScheduleRule``s; a ``ScheduleOverride`` replaces all rules on one date (holiday, illness,
extra session). Every window carries a capacity: how many appointments may start at the same
time. That is a single number because the WxCC queue assigns the actual agent (HANDOVER B5).

    rules (weekly)      overrides (per date)
          │                     │
          └──── windows_on(day) ┘   override for that date? → its windows (or closed)
                     │              otherwise           → the rules valid on that date
                     ▼
              [TimeWindow, ...]  → availability.slots_on()
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.errors import DomainError, ErrorCode

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SLUG_LENGTH = 64
MAX_CAPACITY = 1000
"""Upper bound per window; protects against typos such as 1000000."""


def _invalid(field: str) -> DomainError:
    """Return the error for an invalid configuration ``field``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


class PhoneMode(StrEnum):
    """Whether the booking form asks for a phone number (per group, HANDOVER B15)."""

    HIDDEN = "hidden"
    OPTIONAL = "optional"
    REQUIRED = "required"


@dataclass(frozen=True)
class Organisation:
    """A client organisation; owns groups, branding and integration settings."""

    id: int
    name: str
    slug: str
    default_locale: str = "nl"


@dataclass(frozen=True)
class Group:
    """A bookable team, mapped to one WxCC queue.

    Attributes:
        slug: URL identifier of the standalone page ``/g/{slug}``.
        timezone: IANA zone in which opening hours are expressed.
        slot_minutes: Length of one appointment.
        min_notice_minutes: How long before the start booking closes.
        horizon_days: How far ahead citizens can book.
        phone_mode: Whether the form hides, offers or requires a phone number.
        wxcc_queue_id: Queue that receives the task at T−5 min (ADR 0007).

    Raises:
        DomainError: ``invalid_configuration`` with the offending field.
    """

    id: int
    organisation_id: int
    name: str
    slug: str
    timezone: str
    slot_minutes: int
    min_notice_minutes: int = 60
    horizon_days: int = 60
    phone_mode: PhoneMode = PhoneMode.OPTIONAL
    wxcc_queue_id: str | None = None

    def __post_init__(self) -> None:
        """Validate every field; see the class docstring."""
        if len(self.slug) > MAX_SLUG_LENGTH or not _SLUG.fullmatch(self.slug):
            raise _invalid("slug")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise _invalid("timezone") from exc
        if not 5 <= self.slot_minutes <= 480 or self.slot_minutes % 5:
            raise _invalid("slot_minutes")
        if self.min_notice_minutes < 0:
            raise _invalid("min_notice_minutes")
        if not 1 <= self.horizon_days <= 366:
            raise _invalid("horizon_days")

    @property
    def tz(self) -> ZoneInfo:
        """The group's time zone."""
        return ZoneInfo(self.timezone)


@dataclass(frozen=True, order=True)
class TimeWindow:
    """Local opening hours ``opens``–``closes`` with ``capacity`` parallel appointments."""

    opens: time
    closes: time
    capacity: int

    def __post_init__(self) -> None:
        """Require ``opens < closes`` and ``1 <= capacity <= MAX_CAPACITY``."""
        if self.opens >= self.closes:
            raise _invalid("window")
        if not 1 <= self.capacity <= MAX_CAPACITY:
            raise _invalid("capacity")

    def overlaps(self, other: "TimeWindow") -> bool:
        """Return whether the two windows share any time (touching ends do not count)."""
        return self.opens < other.closes and other.opens < self.closes


@dataclass(frozen=True)
class ScheduleRule:
    """A weekly window: ``weekday`` 0 (Monday) to 6, optionally limited to a date range."""

    weekday: int
    window: TimeWindow
    valid_from: date | None = None
    valid_to: date | None = None

    def __post_init__(self) -> None:
        """Validate the weekday and the validity period."""
        if not 0 <= self.weekday <= 6:
            raise _invalid("weekday")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise _invalid("valid_to")

    def applies_on(self, day: date) -> bool:
        """Return whether this rule opens the group on ``day``."""
        return (
            day.weekday() == self.weekday
            and (self.valid_from is None or day >= self.valid_from)
            and (self.valid_to is None or day <= self.valid_to)
        )

    def _validity_overlaps(self, other: "ScheduleRule") -> bool:
        """Return whether both rules can apply on a common date."""
        starts_before_other_ends = (
            self.valid_from is None or other.valid_to is None or self.valid_from <= other.valid_to
        )
        other_starts_before_self_ends = (
            other.valid_from is None or self.valid_to is None or other.valid_from <= self.valid_to
        )
        return starts_before_other_ends and other_starts_before_self_ends


@dataclass(frozen=True)
class ScheduleOverride:
    """Replaces all rules on ``day``: a window, or ``window=None`` for closed."""

    day: date
    window: TimeWindow | None


def windows_on(
    day: date, rules: Iterable[ScheduleRule], overrides: Iterable[ScheduleOverride]
) -> list[TimeWindow]:
    """Return the opening windows of ``day``, sorted by opening time.

    Overrides for ``day`` win over the rules; a closed override makes the day empty.
    """
    day_overrides = [o for o in overrides if o.day == day]
    if day_overrides:
        if any(o.window is None for o in day_overrides):
            return []
        return sorted(o.window for o in day_overrides if o.window is not None)
    return sorted(rule.window for rule in rules if rule.applies_on(day))


def validate_schedule(rules: Sequence[ScheduleRule], overrides: Sequence[ScheduleOverride]) -> None:
    """Check that no two windows can overlap on any date.

    Raises:
        DomainError: ``invalid_configuration`` with field ``schedule_rules`` or
            ``schedule_overrides``.
    """
    for index, rule in enumerate(rules):
        for other in rules[index + 1 :]:
            if (
                rule.weekday == other.weekday
                and rule._validity_overlaps(other)
                and rule.window.overlaps(other.window)
            ):
                raise _invalid("schedule_rules")
    for index, override in enumerate(overrides):
        for same_day in (o for o in overrides[index + 1 :] if o.day == override.day):
            # A closed day cannot also have windows; windows on one day may not overlap.
            if (
                override.window is None
                or same_day.window is None
                or override.window.overlaps(same_day.window)
            ):
                raise _invalid("schedule_overrides")
