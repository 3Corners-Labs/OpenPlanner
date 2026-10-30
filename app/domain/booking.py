"""Bookings and the participant's contact details.

``Contact`` holds the only personal data in the system (CLAUDE.md privacy rules). Its fields
are excluded from ``repr`` so they cannot end up in logs or tracebacks by accident.

    pending_confirmation ──confirm()──► confirmed      (participant clicks, ADR 0056)
             ├──expire()──► cancelled                  (not confirmed in time)
             └──cancel()──► cancelled

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
from app.domain.form_fields import Answers, normalise_email, normalise_phone
from app.domain.scheduling import PhoneMode

MAX_NAME_LENGTH = 100
DEFAULT_PHONE_REGION = "NL"
"""Region for numbers without a country code when the organisation sets none (ADR 0055)."""
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _invalid(field_name: str) -> DomainError:
    """Return the error for an invalid contact field (the value is never included)."""
    return DomainError(ErrorCode.INVALID_CONTACT, field_name)


def _clean_name(raw: str, field_name: str) -> str:
    """Collapse whitespace; reject empty, too long or control characters."""
    if _CONTROL_CHARS.search(raw):
        raise _invalid(field_name)
    name = " ".join(raw.split())
    if not name or len(name) > MAX_NAME_LENGTH:
        raise _invalid(field_name)
    return name


def _clean_email(raw: str) -> str:
    """Basic structural check, shared with custom email fields (ADR 0055)."""
    return normalise_email(raw, "email")


def _clean_phone(raw: str | None, mode: PhoneMode, region: str) -> str | None:
    """Return the number in E.164 (ADR 0055); apply the group's phone mode."""
    if mode is PhoneMode.HIDDEN:
        return None  # data minimisation: never store what the form does not ask for
    if not (raw or "").strip():
        if mode is PhoneMode.REQUIRED:
            raise _invalid("phone")
        return None
    return normalise_phone(raw or "", region, "phone")


@dataclass(frozen=True)
class Contact:
    """Validated contact details of a participant. Build it with ``Contact.create``.

    ``answers`` are the answers to the group's custom form fields (ADR 0055); they are personal
    data like the rest and are purged with it.
    """

    first_name: str = field(repr=False)
    last_name: str = field(repr=False)
    email: str = field(repr=False)
    phone: str | None = field(repr=False)
    answers: Answers | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls,
        *,
        first_name: str,
        last_name: str,
        email: str,
        phone: str | None,
        phone_mode: PhoneMode,
        region: str = DEFAULT_PHONE_REGION,
        answers: Answers | None = None,
    ) -> "Contact":
        """Validate and normalise raw form input.

        Raises:
            DomainError: ``invalid_contact`` naming the offending field.
        """
        return cls(
            _clean_name(first_name, "first_name"),
            _clean_name(last_name, "last_name"),
            _clean_email(email),
            _clean_phone(phone, phone_mode, region),
            answers,
        )

    @property
    def name(self) -> str:
        """The full name, for the planner's booking page and personal messages."""
        return f"{self.first_name} {self.last_name}".strip()


class BookingKind(StrEnum):
    """A booked slot, or a call started right away (ADR 0019)."""

    SCHEDULED = "scheduled"
    DIRECT = "direct"


@dataclass(frozen=True)
class MeetingRef:
    """The video meeting of a booking: the provider's id and its join link.

    The link is personal data while the booking lives (CLAUDE.md privacy rules: it gives access
    to the participant's call), so it is excluded from ``repr`` and purged with the contact.
    """

    id: str
    link: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TrialCall:
    """A trial call on Webex ahead of the appointment (ADR 0071): meeting and when it closes.

    The meeting's link is personal data like the appointment's own (it gives access to a
    meeting room), so it is excluded from ``repr`` and purged with the contact.
    """

    meeting: MeetingRef
    expires_at: datetime


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
    """A planner asked the participant to choose a new time (ADR 0044); the place is freed."""
    PENDING_CONFIRMATION = "pending_confirmation"
    """The participant has not confirmed by email link yet (ADR 0056); the place is held."""


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
class Claim:
    """Who picked up a booking in the own queue, and when (ADR 0043).

    Attributes:
        claimed_at: When the agent claimed it (UTC).
        claimed_by: Pseudonym of the agent (``tokens.agent_pseudonym``), never a name or
            email address. Removed with the participant's data by ``purge_pii``.
    """

    claimed_at: datetime
    claimed_by: str = field(repr=False)


@dataclass(frozen=True)
class Booking:
    """One participant's appointment in a slot.

    ``contact`` is ``None`` after the personal data has been purged. ``confirm_by`` is set on a
    booking that had to be confirmed by email link (ADR 0056), ``confirmed_at`` once it was:
    the moment the participant's email address was verified.
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
    trial: TrialCall | None = None
    trial_count: int = 0
    wxcc_task_id: str | None = None
    kind: BookingKind = BookingKind.SCHEDULED
    closure_id: int | None = None
    change_reason: ChangeReason | None = None
    rebook_by: datetime | None = None
    claim: Claim | None = None
    confirm_by: datetime | None = None
    confirmed_at: datetime | None = None

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
        """Whether a planner asked the participant to choose a new time (ADR 0044)."""
        return self.status is BookingStatus.AWAITING_REBOOK

    @property
    def awaits_confirmation(self) -> bool:
        """Whether the participant still has to confirm the booking by email link (ADR 0056)."""
        return self.status is BookingStatus.PENDING_CONFIRMATION

    @property
    def lapsed_unconfirmed(self) -> bool:
        """Whether the booking was cancelled because it was not confirmed in time (ADR 0056).

        Told apart from other cancellations without a reason field: it had a deadline to
        confirm, was never confirmed, and was cancelled exactly at that deadline.
        """
        return (
            self.status is BookingStatus.CANCELLED
            and self.confirmed_at is None
            and self.confirm_by is not None
            and self.cancelled_at == self.confirm_by
        )

    @property
    def on_hold(self) -> bool:
        """Whether the booking does not go ahead at its time until the participant or staff act.

        Suspended by a closure (ADR 0024) or awaiting a new time from the participant (ADR 0044):
        either way there is no change deadline, and the participant can choose a new time or
        cancel.
        """
        return self.is_suspended or self.awaits_rebook

    @property
    def holds_place(self) -> bool:
        """Whether the booking takes a place in its slot: confirmed, pending or suspended.

        A booking awaiting confirmation holds its place until it lapses (ADR 0056).

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

    def confirm(self, now: datetime) -> "Booking":
        """Return the booking confirmed by the participant (or a planner on their behalf) at ``now``.

        Raises:
            DomainError: ``booking_not_pending`` if it does not await confirmation.
        """
        if not self.awaits_confirmation:
            raise DomainError(ErrorCode.BOOKING_NOT_PENDING)
        return replace(self, status=BookingStatus.CONFIRMED, confirmed_at=now)

    def expire(self, now: datetime) -> "Booking":
        """Return the booking cancelled because it was not confirmed by ``confirm_by``.

        ``cancelled_at`` is the deadline itself, so ``lapsed_unconfirmed`` can recognise it
        even when the worker ran late.

        Raises:
            DomainError: ``booking_not_pending`` if it does not await confirmation.
        """
        if not self.awaits_confirmation:
            raise DomainError(ErrorCode.BOOKING_NOT_PENDING)
        return replace(self, status=BookingStatus.CANCELLED, cancelled_at=self.confirm_by or now)

    def request_rebook(self, reason: ChangeReason, by: datetime) -> "Booking":
        """Return the booking awaiting a new time from the participant until ``by`` (ADR 0044).

        Its place in the slot is freed at once; the booking keeps its contact details, so the
        participant can choose a new time without entering them again.

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


HOLDS_PLACE = frozenset(
    {BookingStatus.CONFIRMED, BookingStatus.SUSPENDED, BookingStatus.PENDING_CONFIRMATION}
)
"""Statuses that take a place in their slot (counted against its capacity)."""
