"""Group configuration and opening hours.

A ``Group`` is a team (a contact-center queue) participants book with. Its staffing comes from
weekly ``ScheduleRule``s; a ``ScheduleOverride`` replaces all rules on one date (holiday,
illness, extra session). Every window (a staffing block, ADR 0027) carries a capacity: the
number of agents, and so how many appointments may start at the same time. That is a single
number because the contact-center queue assigns the actual agent (HANDOVER B5). A window with 0
agents (lunch, outside office hours) offers nothing, exactly like a gap, but says that it is
deliberate.

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
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.branding import Branding
from app.domain.contact_center import ContactCenterRouting
from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import SUBJECT_CODE, GroupRouting, offers

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_LINE_BREAKS = re.compile(r"[\x85\u2028\u2029]")
MAX_NAME_LENGTH = 200
MIN_SLOT_MINUTES = 5
"""The shortest appointment length, for a group's slots and a flow's duration."""
MAX_SLOT_MINUTES = 480
"""The longest appointment length."""
SLOT_STEP_MINUTES = 5
"""An appointment length is a multiple of this."""
MAX_SLUG_LENGTH = 64
RESERVED_SLUGS = frozenset({"new"})
"""Slugs that are admin paths (``/admin/groups/new``, ``/admin/organisations/new``)."""
MAX_CAPACITY = 1000
"""Upper bound per window; protects against typos such as 1000000."""
MAX_LABEL_LENGTH = 50
"""Longest label of a window ("Lunch", "Teamoverleg"; ADR 0027)."""

MAX_EMAIL_LENGTH = 254
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,63}$")

DEFAULT_NOT_PICKED_UP_MINUTES = 10
MAX_NOT_PICKED_UP_MINUTES = 120
MAX_DIRECT_CALLS = MAX_CAPACITY
"""Upper bound of a group's cap on open direct calls (ADR 0026)."""

DEFAULT_DISPATCH_LEAD_MINUTES = 5
MAX_DISPATCH_LEAD_MINUTES = 60
DEFAULT_PARTICIPANT_CHANGE_LEAD_MINUTES = 60
"""ADR 0069: a participant can move a booking until this long before the start."""
MAX_PARTICIPANT_CHANGE_LEAD_MINUTES = 1440
DEFAULT_TRIAL_CALL_MINUTES = 30
"""ADR 0071: how long a trial call stays open, from the moment the participant starts it."""
MIN_TRIAL_CALL_MINUTES = 10
MAX_TRIAL_CALL_MINUTES = 60
DEFAULT_TRIAL_CALLS_PER_BOOKING = 3
"""ADR 0071: how often one booking may open a trial call; each one is a Webex meeting."""
MAX_TRIAL_CALLS_PER_BOOKING = 5
DEFAULT_QUEUE_VISIBLE_FROM_MINUTES = 15
MAX_QUEUE_VISIBLE_FROM_MINUTES = 120
MAX_PLANNER_LEAD_MINUTES = 1440
"""ADR 0044: upper bound of ``planner_change_min_lead_minutes`` (one day)."""
DEFAULT_MAX_BOOKINGS_PER_EMAIL = 3
"""ADR 0066: future bookings one email address may hold in a group; room to book for others."""
MAX_BOOKINGS_PER_EMAIL = 10
"""ADR 0066: upper bound of ``max_bookings_per_email``; there is no "unlimited" (a protection)."""
DEFAULT_RESCHEDULE_AUTO_DELAY_MINUTES = 15
"""ADR 0057 point 6: how long a shortfall must stay as it is before proposals go out."""
MIN_RESCHEDULE_AUTO_DELAY_MINUTES = 5
MAX_RESCHEDULE_AUTO_DELAY_MINUTES = 120
DEFAULT_RESCHEDULE_AUTO_MAX_BOOKINGS = 10
"""ADR 0057 point 6: above this many bookings automatic mode waits for a planner's review."""
MAX_RESCHEDULE_AUTO_MAX_BOOKINGS = 100


def valid_slot_minutes(minutes: int) -> bool:
    """Return whether ``minutes`` is an allowed appointment length (the one home of the rule)."""
    return MIN_SLOT_MINUTES <= minutes <= MAX_SLOT_MINUTES and minutes % SLOT_STEP_MINUTES == 0


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


def is_slug(text: str) -> bool:
    """Return whether ``text`` is a valid slug: lowercase words joined by hyphens, not too long."""
    return len(text) <= MAX_SLUG_LENGTH and _SLUG.fullmatch(text) is not None


def check_name_and_slug(name: str, slug: str) -> None:
    """Validate the display name and URL slug shared by organisations, groups and calendars.

    Raises:
        DomainError: ``invalid_configuration`` for ``name`` (see ``check_name``) or ``slug``
            (not lowercase words joined by hyphens, too long).
    """
    check_name(name)
    if not is_slug(slug):
        raise _invalid("slug")


class DispatchKind(StrEnum):
    """How a group's bookings reach its staff (ADR 0043)."""

    CONTACT_CENTER = "contact_center"
    """A task in the contact-center queue at the dispatch lead (ADR 0007, 0032)."""
    OPENPLANNER_QUEUE = "openplanner_queue"
    """OpenPlanner's own queue: agents sign in and pick up a booking."""


class DispatchFallback(StrEnum):
    """What a contact-center group falls back to while the contact center fails (ADR 0082)."""

    NONE = "none"
    """No fallback: a failed dispatch stays a failed dispatch."""
    OWN_QUEUE = "own_queue"
    """The own queue of ADR 0043: a booking whose dispatch fails, and while the contact center
    is down every booking, appears there, flagged as fallback."""


ROOM_ID = re.compile(r"^[A-Za-z0-9_=-]{10,200}$")
"""A Webex room id, as the Messaging API gives it (base64url)."""


class JoinClient(StrEnum):
    """Which Webex page a participant's join link opens (ADR 0075, per group)."""

    BROWSER = "browser"
    """The Webex web app straight away; on a computer there is no app to choose or install."""
    CHOICE = "choice"
    """Webex's own page, which offers the app or the browser (the stored link, unchanged)."""


class Offer(StrEnum):
    """What a staffing block offers participants (ADR 0072): slots, direct calls, or both."""

    SCHEDULED = "scheduled"
    """Appointments in the slot grid; the default."""
    DIRECT = "direct"
    """Direct calls (ADR 0019) only; the block offers no slots."""
    BOTH = "both"

    @property
    def scheduled(self) -> bool:
        """Whether the block takes part in the slot grid."""
        return self is not Offer.DIRECT

    @property
    def direct(self) -> bool:
        """Whether a participant can start a direct call during the block."""
        return self is not Offer.SCHEDULED


class PhoneMode(StrEnum):
    """Whether the booking form asks for a phone number (per group, HANDOVER B15)."""

    HIDDEN = "hidden"
    OPTIONAL = "optional"
    REQUIRED = "required"


@dataclass(frozen=True)
class Organisation:
    """A client organisation; owns groups, branding and integration settings.

    The slug never changes once created: admin URLs refer to it. The identity provider
    names the organisation by ``idp_key`` (ADR 0049 point 4), which starts as the slug and
    is set by the superuser from then on.
    """

    id: int
    name: str
    slug: str
    default_locale: str = "nl"
    branding: Branding = Branding()
    idp_key: str = ""

    def __post_init__(self) -> None:
        """Validate name and slug; the locale is checked against the catalogues by the service.

        An empty ``idp_key`` is the slug (what a new organisation starts with).
        """
        check_name_and_slug(self.name, self.slug)
        if not self.idp_key:
            object.__setattr__(self, "idp_key", self.slug)


@dataclass(frozen=True)
class Group:
    """A bookable team, mapped to one contact-center queue.

    Attributes:
        slug: URL identifier of the standalone page ``/g/{slug}``.
        timezone: IANA zone in which opening hours are expressed.
        slot_minutes: Length of one appointment.
        min_notice_minutes: How long before the start booking closes.
        horizon_days: How far ahead participants can book.
        phone_mode: Whether the form hides, offers or requires a phone number.
        video_host_email: Licensed Webex host on whose behalf meetings are scheduled
            (ADR 0017); without it the group has no video meetings.
        contact_center: The group's routing at its contact-center platform (ADR 0032): the
            platform, the routing value (the slug when empty) and the platform's attributes,
            validated against its descriptor; ``None`` when nothing is configured.
        not_picked_up_minutes: After this long without an agent a call counts as not
            picked up (signal for O2).
        direct_call_max_concurrent: How many direct calls may be open (waiting or with an
            agent) at once, typically the number of agents for direct calls; ``None`` leaves
            it to the contact center (ADR 0026).
        dispatch_lead_minutes: How long before the start the contact-center task is created
            (``job_plan.dispatch_moment``). At most ``min_notice_minutes``, so a booking is
            never dispatched the moment it is made. Used only with
            ``DispatchKind.CONTACT_CENTER``.
        dispatch_kind: Contact center or OpenPlanner's own queue (ADR 0043).
        participant_change_lead_minutes: How long before the start the participant may still move
            the booking (ADR 0069, class D per group); cancelling is possible until the start.
            Never below ``dispatch_lead_minutes``. A booking made later than that cannot be
            moved by the participant, only cancelled.
        planner_change_min_lead_minutes: How long before the start a planner may still move
            or cancel a booking (ADR 0044, class D per group). Never below
            ``dispatch_lead_minutes``: a dispatched booking needs a phone call, not a mail.
        trial_call_enabled: Whether the participant can open a trial call on Webex ahead of the
            appointment (ADR 0071); needs video. On by default.
        trial_call_minutes: How long one trial call stays open, 10 to 60 minutes.
        trial_calls_per_booking: How many trial calls one booking may open, 1 to 5, so the page
            cannot be used to create Webex meetings without end.
        join_client: Which Webex page the participant's link to the appointment and the trial call
            opens (ADR 0075): the web app straight away (the default) or Webex's own choice
            between app and browser.
        embedding_enabled: Whether the group may appear in the widget on the websites its
            organisation allows (ADR 0035); off until the group switches it on.
        queue_visible_from_minutes: Own queue: how long before its start a booking appears.
        queue_free_pick_allowed: Own queue: whether agents may claim any row, not only the
            next one (a working agreement against cherry picking, not a protection).
        max_bookings_per_email: How many future bookings that hold a place one email address
            may have in this group, 1 to ``MAX_BOOKINGS_PER_EMAIL`` (ADR 0066). Above 1 so
            participants can book on behalf of others; the same address twice in one slot is
            always refused.
        reschedule_auto_send: Whether the rebooking assistant sends its proposals without a
            planner's review (ADR 0057 point 6); off keeps the review.
        reschedule_auto_delay_minutes: How long a shortfall must stay the same before the
            automatic proposals go out, so a typo in the staffing can be undone first.
        reschedule_auto_max_bookings: Above this many affected bookings automatic mode sends
            nothing and asks a planner to review.
        dispatch_fallback: What a contact-center group falls back to while the contact center
            fails (ADR 0082 point 11): nothing, or the own queue.
        fallback_space_room_id: The team's own Webex space for fallback messages (ADR 0082
            point 12), posted to by the organisation's bot; ``None`` posts nothing per booking.
        fallback_active_since: Since when the whole group is in fallback because the
            organisation's contact center is down (state, written by the worker), or ``None``.
        calendar_invite_address: Own queue: the team's shared mailbox or list that gets an
            iCalendar invite per booking (ADR 0043 point 9); ``None`` sends none.
        idp_key: The value the identity provider names the group by in an entitlement
            (ADR 0049 point 4); the slug until the organisation admin sets it.
        archived_at: When the group was archived (ADR 0084 point 3), or ``None`` while it is
            in use. An archived group takes no bookings, answers 404 on its public page, is
            read-only in the portal and keeps its slug until the nightly sweep erases it.
        routing: Subjects, switched-on routing attributes and their mapping to the contact
            center (ADR 0081); empty by default, and then the form is as before.

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
    contact_center: ContactCenterRouting | None = None
    not_picked_up_minutes: int = DEFAULT_NOT_PICKED_UP_MINUTES
    direct_call_max_concurrent: int | None = None
    dispatch_lead_minutes: int = DEFAULT_DISPATCH_LEAD_MINUTES
    dispatch_kind: DispatchKind = DispatchKind.CONTACT_CENTER
    participant_change_lead_minutes: int = DEFAULT_PARTICIPANT_CHANGE_LEAD_MINUTES
    planner_change_min_lead_minutes: int = 60
    trial_call_enabled: bool = True
    trial_call_minutes: int = DEFAULT_TRIAL_CALL_MINUTES
    trial_calls_per_booking: int = DEFAULT_TRIAL_CALLS_PER_BOOKING
    join_client: JoinClient = JoinClient.BROWSER
    embedding_enabled: bool = False
    queue_visible_from_minutes: int = DEFAULT_QUEUE_VISIBLE_FROM_MINUTES
    queue_free_pick_allowed: bool = False
    max_bookings_per_email: int = DEFAULT_MAX_BOOKINGS_PER_EMAIL
    reschedule_auto_send: bool = False
    reschedule_auto_delay_minutes: int = DEFAULT_RESCHEDULE_AUTO_DELAY_MINUTES
    reschedule_auto_max_bookings: int = DEFAULT_RESCHEDULE_AUTO_MAX_BOOKINGS
    calendar_invite_address: str | None = None
    dispatch_fallback: DispatchFallback = DispatchFallback.NONE
    fallback_space_room_id: str | None = None
    fallback_active_since: datetime | None = None
    idp_key: str = ""
    archived_at: datetime | None = None
    routing: GroupRouting = field(default_factory=GroupRouting)

    @property
    def is_archived(self) -> bool:
        """Return whether the group is archived (ADR 0084)."""
        return self.archived_at is not None

    def __post_init__(self) -> None:
        """Validate every field; see the class docstring."""
        check_name_and_slug(self.name, self.slug)
        if not self.idp_key:
            object.__setattr__(self, "idp_key", self.slug)
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise _invalid("timezone") from exc
        if not valid_slot_minutes(self.slot_minutes):
            raise _invalid("slot_minutes")
        if self.min_notice_minutes < 0:
            raise _invalid("min_notice_minutes")
        if not 1 <= self.horizon_days <= 366:
            raise _invalid("horizon_days")
        if not 0 <= self.dispatch_lead_minutes <= MAX_DISPATCH_LEAD_MINUTES:
            raise _invalid("dispatch_lead_minutes")
        if self.min_notice_minutes < self.dispatch_lead_minutes:
            raise _invalid("min_notice_minutes")
        change_lead = self.participant_change_lead_minutes
        if not self.dispatch_lead_minutes <= change_lead <= MAX_PARTICIPANT_CHANGE_LEAD_MINUTES:
            raise _invalid("participant_change_lead_minutes")
        planner_lead = self.planner_change_min_lead_minutes
        if not self.dispatch_lead_minutes <= planner_lead <= MAX_PLANNER_LEAD_MINUTES:
            raise _invalid("planner_change_min_lead_minutes")
        if not MIN_TRIAL_CALL_MINUTES <= self.trial_call_minutes <= MAX_TRIAL_CALL_MINUTES:
            raise _invalid("trial_call_minutes")
        if not 1 <= self.trial_calls_per_booking <= MAX_TRIAL_CALLS_PER_BOOKING:
            raise _invalid("trial_calls_per_booking")
        if not 0 <= self.queue_visible_from_minutes <= MAX_QUEUE_VISIBLE_FROM_MINUTES:
            raise _invalid("queue_visible_from_minutes")
        if not 1 <= self.max_bookings_per_email <= MAX_BOOKINGS_PER_EMAIL:
            raise _invalid("max_bookings_per_email")
        delay = self.reschedule_auto_delay_minutes
        if not MIN_RESCHEDULE_AUTO_DELAY_MINUTES <= delay <= MAX_RESCHEDULE_AUTO_DELAY_MINUTES:
            raise _invalid("reschedule_auto_delay_minutes")
        if not 1 <= self.reschedule_auto_max_bookings <= MAX_RESCHEDULE_AUTO_MAX_BOOKINGS:
            raise _invalid("reschedule_auto_max_bookings")
        if self.video_host_email is not None and not _EMAIL.fullmatch(self.video_host_email):
            raise _invalid("video_host_email")
        room = self.fallback_space_room_id
        if room is not None and not ROOM_ID.fullmatch(room):
            raise _invalid("fallback_space_room_id")
        invite = self.calendar_invite_address
        if invite is not None and (len(invite) > MAX_EMAIL_LENGTH or not _EMAIL.fullmatch(invite)):
            raise _invalid("calendar_invite_address")
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
        """Whether this group's bookings are sent to the contact center as tasks."""
        return (
            self.dispatch_kind is DispatchKind.CONTACT_CENTER
            and self.contact_center is not None
            and self.contact_center.complete
        )

    @property
    def routing_value(self) -> str:
        """The value the contact center's flow routes on: the configured one, or the slug."""
        return self.contact_center.value_for(self.slug) if self.contact_center else self.slug

    @property
    def tz(self) -> ZoneInfo:
        """The group's time zone."""
        return ZoneInfo(self.timezone)

    def today(self, now: datetime) -> date:
        """Return the date of ``now`` in the group's time zone."""
        return now.astimezone(self.tz).date()

    @property
    def dispatch_lead(self) -> timedelta:
        """How long before the start the contact-center task is created."""
        return timedelta(minutes=self.dispatch_lead_minutes)

    @property
    def planner_lead(self) -> timedelta:
        """How long before the start a planner may last change a booking (ADR 0044)."""
        return timedelta(minutes=self.planner_change_min_lead_minutes)

    @property
    def has_own_queue(self) -> bool:
        """Whether agents pick up this group's bookings in OpenPlanner (ADR 0043)."""
        return self.dispatch_kind is DispatchKind.OPENPLANNER_QUEUE

    @property
    def falls_back(self) -> bool:
        """Whether this contact-center group may fall back to the own queue (ADR 0082)."""
        return self.dispatch_kind is DispatchKind.CONTACT_CENTER and (
            self.dispatch_fallback is DispatchFallback.OWN_QUEUE
        )

    @property
    def fallback_active(self) -> bool:
        """Whether the whole group is in fallback now (the contact center is down)."""
        return self.falls_back and self.fallback_active_since is not None

    @property
    def queue_open(self) -> bool:
        """Whether the group has a queue in OpenPlanner: its own, or one to fall back to."""
        return self.has_own_queue or self.falls_back

    @property
    def sends_team_invite(self) -> bool:
        """Whether each booking is also sent as a calendar invite to the team (ADR 0043)."""
        return self.has_own_queue and self.calendar_invite_address is not None

    def change_deadline(self, start: datetime) -> datetime:
        """Return the moment the participant can no longer move a booking starting at ``start``.

        ``participant_change_lead_minutes`` before the start, for both dispatch kinds (ADR 0069).
        A booking already dispatched to the contact center is moved with its task ended
        (ADR 0021), so the lead is never shorter than the dispatch lead.
        """
        return start - timedelta(minutes=self.participant_change_lead_minutes)

    def cancel_deadline(self, start: datetime) -> datetime:
        """Return the moment the participant can no longer cancel: the start itself (ADR 0069).

        A late cancellation beats a no-show; after dispatch the task is marked cancelled
        (ADR 0021) and the team's invite withdrawn (ADR 0043).
        """
        return start


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
            participants or logged, and no part of equality or ordering.
        offer: Slots, direct calls or both (ADR 0072); ``scheduled`` by default.
        subjects: The codes of the group's subjects this block offers (ADR 0081 point 4),
            sorted; empty offers every subject. A code the group no longer has offers nothing.
    """

    opens: time
    closes: time
    capacity: int
    label: str | None = field(default=None, compare=False)
    offer: Offer = Offer.SCHEDULED
    subjects: tuple[str, ...] = ()

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
        if tuple(sorted(set(self.subjects))) != self.subjects or any(
            not SUBJECT_CODE.fullmatch(code) for code in self.subjects
        ):
            raise _invalid("subjects")

    def offers_subject(self, code: str | None) -> bool:
        """Return whether a participant who chose ``code`` may book in this block.

        A block without subjects offers all of them, and a booking without a subject (the
        group asks none) fits every block.
        """
        return offers(self.subjects, code)

    @property
    def staffed(self) -> bool:
        """Whether at least one agent works in this block."""
        return self.capacity > 0

    @property
    def offers_slots(self) -> bool:
        """Whether the block takes part in the slot grid: staffed and not direct-only."""
        return self.staffed and self.offer.scheduled

    @property
    def offers_direct(self) -> bool:
        """Whether a participant can start a direct call during the block (ADR 0072)."""
        return self.staffed and self.offer.direct

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

    def applies_from(self, day: date) -> bool:
        """Return whether this rule still opens the group on ``day`` or a later day."""
        return self.valid_to is None or self.valid_to >= day

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


def split_block(
    old: ScheduleRule, at: time, capacity: int, label: str | None
) -> tuple[ScheduleRule, ScheduleRule]:
    """Split a weekly block at ``at`` into two touching blocks (ADR 0027), without ids.

    The first part keeps the block's count and label and ends at ``at``; the second part runs
    from ``at`` to the old end with ``capacity`` and ``label``. Both keep the weekday, the
    validity period, the offer and the subjects. The caller validates the whole schedule.

    Raises:
        DomainError: ``invalid_configuration`` (``split_at``) unless ``at`` lies strictly inside
            the block; the new count and label are checked by ``TimeWindow``.
    """
    if not old.window.opens < at < old.window.closes:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "split_at")
    first = replace(old, window=replace(old.window, closes=at), id=None)
    second = replace(
        old, window=replace(old.window, opens=at, capacity=capacity, label=label), id=None
    )
    return first, second


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


def open_for_direct(
    group: Group,
    rules: Iterable[ScheduleRule],
    overrides: Iterable[ScheduleOverride],
    now: datetime,
    subject: str | None = None,
) -> bool:
    """Return whether ``now`` falls inside a staffed block that offers direct calls (ADR 0072).

    With ``subject`` the block must offer that subject too (ADR 0081 point 4).
    """
    local = now.astimezone(group.tz)
    return any(
        window.offers_direct
        and window.offers_subject(subject)
        and window.opens <= local.time() < window.closes
        for window in windows_on(local.date(), rules, overrides)
    )
