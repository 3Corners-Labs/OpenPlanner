"""Bookings and the citizen's contact details.

``Contact`` holds the only personal data in the system (CLAUDE.md privacy rules). Its fields
are excluded from ``repr`` so they cannot end up in logs or tracebacks by accident.

    confirmed ──cancel()──► cancelled
        │  ▲
        │  └──reinstate()── suspended ──cancel()──► cancelled
        ├──suspend()──────────►┘   (emergency closure, ADR 0024)
        └──(after the call, WP4/WP6)──► completed | no_show
"""

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import PhoneMode

MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 254
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_PHONE_SEPARATORS = re.compile(r"[\s\-().]")
_PHONE = re.compile(r"^\+?[0-9]{8,15}$")


def _invalid(field_name: str) -> DomainError:
    """Return the error for an invalid contact field (the value is never included)."""
    return DomainError(ErrorCode.INVALID_CONTACT, field_name)


def _clean_name(raw: str) -> str:
    """Collapse whitespace; reject empty, too long or control characters."""
    if _CONTROL_CHARS.search(raw):
        raise _invalid("name")
    name = " ".join(raw.split())
    if not name or len(name) > MAX_NAME_LENGTH:
        raise _invalid("name")
    return name


def _clean_email(raw: str) -> str:
    """Basic structural check; full validation (MX, IDNA) happens at the HTTP edge."""
    email = raw.strip()
    local, at, domain = email.partition("@")
    if (
        not at
        or not local
        or "@" in domain
        or "." not in domain.strip(".")
        or len(email) > MAX_EMAIL_LENGTH
        or any(ch.isspace() for ch in email)
        or _CONTROL_CHARS.search(email)
    ):
        raise _invalid("email")
    return email


def _clean_phone(raw: str | None, mode: PhoneMode) -> str | None:
    """Normalise to digits with an optional leading ``+``; apply the group's phone mode."""
    if mode is PhoneMode.HIDDEN:
        return None  # data minimisation: never store what the form does not ask for
    phone = _PHONE_SEPARATORS.sub("", raw or "")
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    if not phone:
        if mode is PhoneMode.REQUIRED:
            raise _invalid("phone")
        return None
    if not _PHONE.fullmatch(phone):
        raise _invalid("phone")
    return phone


@dataclass(frozen=True)
class Contact:
    """Validated contact details of a citizen. Build it with ``Contact.create``."""

    name: str = field(repr=False)
    email: str = field(repr=False)
    phone: str | None = field(repr=False)

    @classmethod
    def create(
        cls, *, name: str, email: str, phone: str | None, phone_mode: PhoneMode
    ) -> "Contact":
        """Validate and normalise raw form input.

        Raises:
            DomainError: ``invalid_contact`` naming the offending field.
        """
        return cls(_clean_name(name), _clean_email(email), _clean_phone(phone, phone_mode))

    @property
    def first_name(self) -> str:
        """The first word of the name; the only name part sent to WxCC (CLAUDE.md)."""
        return self.name.split()[0]


class BookingKind(StrEnum):
    """A booked slot, or a call started right away (ADR 0019)."""

    SCHEDULED = "scheduled"
    DIRECT = "direct"


@dataclass(frozen=True)
class MeetingRef:
    """The video meeting of a booking: the provider's id and its join link.

    The link is personal data while the booking lives (CLAUDE.md privacy rules: it gives access
    to the citizen's call), so it is excluded from ``repr`` and purged with the contact.
    """

    id: str
    link: str | None = field(default=None, repr=False)


class ChangeReason(StrEnum):
    """Why a planner cancelled a booking or asked to rebook it (ADR 0044).

    A fixed list, never free text, so no personal data reaches a message through a planner's
    typing. Each reason is an i18n text block ``message.reason.<value>`` with organisation
    overrides.
    """

    STAFFING = "staffing"
    TECHNICAL = "technical"
    UNFORESEEN = "unforeseen"


class BookingStatus(StrEnum):
    """Lifecycle of a booking."""

    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    SUSPENDED = "suspended"
    AWAITING_REBOOK = "awaiting_rebook"
    """A planner asked the citizen to choose a new time (ADR 0044); the place is freed."""


@dataclass(frozen=True)
class Slot:
    """A start time of a group that has at least one booking; created by the first one.

    ``capacity_snapshot`` is the capacity at creation; later schedule edits do not change it.
    A direct call has its own slot (``kind`` direct) that takes no scheduled capacity.
    """

    id: int | None
    group_id: int
    start: datetime
    end: datetime
    capacity_snapshot: int
    kind: BookingKind = BookingKind.SCHEDULED


@dataclass(frozen=True)
class SlotUsage:
    """How full an existing slot is: its capacity snapshot and active bookings."""

    start: datetime
    capacity_snapshot: int
    active: int


@dataclass(frozen=True)
class Booking:
    """One citizen's appointment in a slot.

    ``contact`` is ``None`` after the personal data has been purged.
    """

    id: int | None
    group_id: int
    slot_id: int
    start: datetime
    end: datetime
    status: BookingStatus
    contact: Contact | None = field(repr=False)
    locale: str
    token_hash: str = field(repr=False)
    created_at: datetime
    token_salt: str = field(default="", repr=False)
    consents: frozenset[ChannelKind] = frozenset()
    cancelled_at: datetime | None = None
    rescheduled_from_id: int | None = None
    meeting: MeetingRef | None = None
    wxcc_task_id: str | None = None
    kind: BookingKind = BookingKind.SCHEDULED
    closure_id: int | None = None
    change_reason: ChangeReason | None = None
    rebook_by: datetime | None = None

    @property
    def reference(self) -> str:
        """Human-readable booking reference for agents and reports, e.g. ``OP-000123``."""
        return f"OP-{self.id or 0:06d}"

    @property
    def is_active(self) -> bool:
        """Whether the appointment goes ahead (reminders, meeting, WxCC task)."""
        return self.status is BookingStatus.CONFIRMED

    @property
    def is_suspended(self) -> bool:
        """Whether an emergency closure put the appointment on hold (ADR 0024)."""
        return self.status is BookingStatus.SUSPENDED

    @property
    def awaits_rebook(self) -> bool:
        """Whether a planner asked the citizen to choose a new time (ADR 0044)."""
        return self.status is BookingStatus.AWAITING_REBOOK

    @property
    def on_hold(self) -> bool:
        """Whether the booking does not go ahead at its time until the citizen or staff act.

        Suspended by a closure (ADR 0024) or awaiting a new time from the citizen (ADR 0044):
        either way there is no change deadline, and the citizen can choose a new time or
        cancel.
        """
        return self.is_suspended or self.awaits_rebook

    @property
    def holds_place(self) -> bool:
        """Whether the booking takes a place in its slot: confirmed, or on hold.

        A suspended booking keeps its place, so it can be reinstated when the closure ends
        early without competing with new bookings.
        """
        return self.status in HOLDS_PLACE

    def cancel(self, now: datetime, reason: ChangeReason | None = None) -> "Booking":
        """Return the cancelled booking; ``reason`` when a planner cancelled it (ADR 0044).

        A booking awaiting a new time keeps the reason it was given when it is cancelled.

        Raises:
            DomainError: ``booking_not_active`` if it is not confirmed, suspended or awaiting
                a new time.
        """
        if not self.holds_place and not self.awaits_rebook:
            raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
        return replace(
            self,
            status=BookingStatus.CANCELLED,
            cancelled_at=now,
            change_reason=reason or self.change_reason,
        )

    def request_rebook(self, reason: ChangeReason, by: datetime) -> "Booking":
        """Return the booking awaiting a new time from the citizen until ``by`` (ADR 0044).

        Its place in the slot is freed at once; the booking keeps its contact details, so the
        citizen can choose a new time without entering them again.

        Raises:
            DomainError: ``booking_not_active`` if it is not confirmed.
        """
        if not self.is_active:
            raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
        return replace(
            self, status=BookingStatus.AWAITING_REBOOK, change_reason=reason, rebook_by=by
        )

    def suspend(self, closure_id: int) -> "Booking":
        """Return the booking put on hold by closure ``closure_id``.

        Raises:
            DomainError: ``booking_not_active`` if it is not confirmed.
        """
        if not self.is_active:
            raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
        return replace(self, status=BookingStatus.SUSPENDED, closure_id=closure_id)

    def reinstate(self) -> "Booking":
        """Return the suspended booking confirmed again at its original time.

        Raises:
            DomainError: ``booking_not_active`` if it is not suspended.
        """
        if not self.is_suspended:
            raise DomainError(ErrorCode.BOOKING_NOT_ACTIVE)
        return replace(self, status=BookingStatus.CONFIRMED)


HOLDS_PLACE = frozenset({BookingStatus.CONFIRMED, BookingStatus.SUSPENDED})
"""Statuses that take a place in their slot (counted against its capacity)."""
