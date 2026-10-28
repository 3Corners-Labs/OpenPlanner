"""Group configuration and opening hours.

A ``Group`` is a team (a WxCC queue) citizens book with. Its staffing comes from weekly
``ScheduleRule``s; a ``ScheduleOverride`` replaces all rules on one date (holiday, illness,
extra session). Every window (a staffing block, ADR 0027) carries a capacity: the number of
agents, and so how many appointments may start at the same time. That is a single number
because the WxCC queue assigns the actual agent (HANDOVER B5). A window with 0 agents (lunch,
outside office hours) offers nothing, exactly like a gap, but says that it is deliberate.

    rules (weekly)      overrides (per date)
          │                     │
          └──── windows_on(day) ┘   override for that date? → its windows (or closed)
                     │              otherwise           → the rules valid on that date
                     ▼
              [TimeWindow, ...]  → availability.slots_on()
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.branding import Branding
from app.domain.errors import DomainError, ErrorCode

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_LINE_BREAKS = re.compile(r"[\x85\u2028\u2029]")
MAX_NAME_LENGTH = 200
MAX_SLUG_LENGTH = 64
RESERVED_SLUGS = frozenset({"new"})
"""Slugs that are admin paths (``/admin/groups/new``, ``/admin/organisations/new``)."""
MAX_CAPACITY = 1000
"""Upper bound per window; protects against typos such as 1000000."""
MAX_LABEL_LENGTH = 50
"""Longest label of a window ("Lunch", "Teamoverleg"; ADR 0027)."""

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,63}$")

INTEGRATION_TEXT_LIMITS = {
    "wxcc_business_address": 255,
    "wxcc_channel": 255,
    "wxcc_routing_value": 512,
}
"""Maximum lengths (WxCC limits: flowSettings values up to 512 bytes)."""

DEFAULT_NOT_PICKED_UP_MINUTES = 10
MAX_NOT_PICKED_UP_MINUTES = 120
MAX_DIRECT_CALLS = MAX_CAPACITY
"""Upper bound of a group's cap on open direct calls (ADR 0026)."""

DEFAULT_DISPATCH_LEAD_MINUTES = 5
MAX_DISPATCH_LEAD_MINUTES = 60


def _invalid(field: str) -> DomainError:
    """Return the error for an invalid configuration ``field``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


def check_name(name: str) -> None:
    """Validate a display name: not blank, at most ``MAX_NAME_LENGTH``, no control characters.

    Raises:
        DomainError: ``invalid_configuration`` with field ``name``.
    """
    if not name.strip() or len(name) > MAX_NAME_LENGTH or _CONTROL_CHARS.search(name):
        raise _invalid("name")


def slug_from(text: str) -> str:
    """Return ``text`` as a slug: lowercase ASCII words joined by hyphens, at most 64 characters.

    Accents are dropped (``Café`` → ``cafe``), every run of other characters becomes one hyphen,
    and a too-long result is cut at the last hyphen within the limit. Returns ``""`` when nothing
    usable is left; ``check_name_and_slug`` then refuses it.
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = _NOT_SLUG.sub("-", ascii_text.lower()).strip("-")
    if len(slug) <= MAX_SLUG_LENGTH:
        return slug
    cut = slug[: MAX_SLUG_LENGTH + 1]
    return cut[: cut.rfind("-")] if "-" in cut else slug[:MAX_SLUG_LENGTH]


def check_name_and_slug(name: str, slug: str) -> None:
    """Validate the display name and URL slug shared by organisations, groups and calendars.

    Raises:
        DomainError: ``invalid_configuration`` for ``name`` (see ``check_name``) or ``slug``
            (not lowercase words joined by hyphens, too long).
    """
    check_name(name)
    if len(slug) > MAX_SLUG_LENGTH or not _SLUG.fullmatch(slug):
        raise _invalid("slug")


class PhoneMode(StrEnum):
    """Whether the booking form asks for a phone number (per group, HANDOVER B15)."""

    HIDDEN = "hidden"
    OPTIONAL = "optional"
    REQUIRED = "required"


@dataclass(frozen=True)
class Organisation:
    """A client organisation; owns groups, branding and integration settings.

    The slug never changes once created: admin URLs and the organisation claim of the
    identity provider (ADR 0013) refer to it.
    """

    id: int
    name: str
    slug: str
    default_locale: str = "nl"
    branding: Branding = Branding()

    def __post_init__(self) -> None:
        """Validate name and slug; the locale is checked against the catalogues by the service."""
        check_name_and_slug(self.name, self.slug)


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
        video_host_email: Licensed Webex host on whose behalf meetings are scheduled
            (ADR 0017); without it the group has no video meetings.
        wxcc_business_address: Business address of the WxCC work item asset (ADR 0018).
        wxcc_channel: Name of the WxCC work item channel.
        wxcc_routing_value: Passed to the WxCC flow to choose the queue; the slug when empty.
        direct_call_enabled: Whether citizens can start a call right away (ADR 0019).
        not_picked_up_minutes: After this long without an agent a call counts as not
            picked up (signal for O2).
        direct_call_max_concurrent: How many direct calls may be open (waiting or with an
            agent) at once, typically the number of agents for direct calls; ``None`` leaves
            it to WxCC (ADR 0026).
        dispatch_lead_minutes: How long before the start the WxCC task is created; from
            that moment the booking is read-only (``job_plan.change_deadline``). At most
            ``min_notice_minutes``, so no booking is read-only when it is made.

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
    video_host_email: str | None = None
    wxcc_business_address: str | None = None
    wxcc_channel: str | None = None
    wxcc_routing_value: str | None = None
    direct_call_enabled: bool = False
    not_picked_up_minutes: int = DEFAULT_NOT_PICKED_UP_MINUTES
    direct_call_max_concurrent: int | None = None
    dispatch_lead_minutes: int = DEFAULT_DISPATCH_LEAD_MINUTES

    def __post_init__(self) -> None:
        """Validate every field; see the class docstring."""
        check_name_and_slug(self.name, self.slug)
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
        if not 0 <= self.dispatch_lead_minutes <= MAX_DISPATCH_LEAD_MINUTES:
            raise _invalid("dispatch_lead_minutes")
        if self.min_notice_minutes < self.dispatch_lead_minutes:
            raise _invalid("min_notice_minutes")
        if self.video_host_email is not None and not _EMAIL.fullmatch(self.video_host_email):
            raise _invalid("video_host_email")
        for name, limit in INTEGRATION_TEXT_LIMITS.items():
            value = getattr(self, name)
            if value is not None and (
                not value.strip() or len(value) > limit or _CONTROL_CHARS.search(value)
            ):
                raise _invalid(name)
        if not 1 <= self.not_picked_up_minutes <= MAX_NOT_PICKED_UP_MINUTES:
            raise _invalid("not_picked_up_minutes")
        cap = self.direct_call_max_concurrent
        if cap is not None and not 1 <= cap <= MAX_DIRECT_CALLS:
            raise _invalid("direct_call_max_concurrent")

    @property
    def video_enabled(self) -> bool:
        """Whether meetings are created for this group's bookings."""
        return self.video_host_email is not None

    @property
    def dispatch_enabled(self) -> bool:
        """Whether this group's bookings are sent to WxCC as work items."""
        return self.wxcc_business_address is not None and self.wxcc_channel is not None

    @property
    def routing_value(self) -> str:
        """The value the WxCC flow routes on: the configured one, or the slug."""
        return self.wxcc_routing_value or self.slug

    @property
    def tz(self) -> ZoneInfo:
        """The group's time zone."""
        return ZoneInfo(self.timezone)

    def today(self, now: datetime) -> date:
        """Return the date of ``now`` in the group's time zone."""
        return now.astimezone(self.tz).date()

    @property
    def dispatch_lead(self) -> timedelta:
        """How long before the start the WxCC task is created."""
        return timedelta(minutes=self.dispatch_lead_minutes)


@dataclass(frozen=True)
class GroupSummary:
    """A group with the slug and name of its organisation, for admin listings."""

    group: Group
    organisation_slug: str
    organisation_name: str


@dataclass(frozen=True, order=True)
class TimeWindow:
    """A staffing block ``opens``–``closes`` (local) with ``capacity`` agents (ADR 0027).

    Attributes:
        capacity: Number of agents, 0 to ``MAX_CAPACITY``; 0 offers no slots (lunch).
        label: Optional admin note such as "Lunch"; admin content only, never shown to
            citizens or logged, and no part of equality or ordering.
    """

    opens: time
    closes: time
    capacity: int
    label: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        """Require ``opens < closes``, ``0 <= capacity <= MAX_CAPACITY`` and a valid label."""
        if self.opens >= self.closes:
            raise _invalid("window")
        if not 0 <= self.capacity <= MAX_CAPACITY:
            raise _invalid("capacity")
        if self.label is not None and (
            not self.label.strip()
            or len(self.label) > MAX_LABEL_LENGTH
            or _CONTROL_CHARS.search(self.label)
            or _LINE_BREAKS.search(self.label)
        ):
            raise _invalid("label")

    @property
    def staffed(self) -> bool:
        """Whether at least one agent works in this block."""
        return self.capacity > 0

    def overlaps(self, other: "TimeWindow") -> bool:
        """Return whether the two windows share any time (touching ends do not count)."""
        return self.opens < other.closes and other.opens < self.closes


@dataclass(frozen=True)
class ScheduleRule:
    """A weekly window: ``weekday`` 0 (Monday) to 6, optionally limited to a date range.

    ``id`` identifies a stored rule (for removal); it takes no part in equality.
    """

    weekday: int
    window: TimeWindow
    valid_from: date | None = None
    valid_to: date | None = None
    id: int | None = field(default=None, compare=False)

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
    """Replaces all rules on ``day``: a window, or ``window=None`` for closed.

    ``id`` identifies a stored override (for removal); it takes no part in equality.
    """

    day: date
    window: TimeWindow | None
    id: int | None = field(default=None, compare=False)


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


def is_open(
    group: Group,
    rules: Iterable[ScheduleRule],
    overrides: Iterable[ScheduleOverride],
    now: datetime,
) -> bool:
    """Return whether ``now`` falls inside a staffed window of the group (local time)."""
    local = now.astimezone(group.tz)
    return any(
        window.staffed and window.opens <= local.time() < window.closes
        for window in windows_on(local.date(), rules, overrides)
    )
