"""Ports: Protocols for everything outside the domain.

Adapters in ``app.adapters`` implement them; tests use the fakes in ``tests/fakes``.
External systems still to come with their work package: ``VideoProvider`` (WP5, including
live meeting status per ADR 0010), ``ContactCenterDispatcher`` (WP6, ADR 0007),
``IdentityProvider`` (WP7).
"""

from collections.abc import Collection, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from app.domain.agenda import ShortfallAcceptance
from app.domain.api_clients import AccessToken, ApiClient, ApiSettings
from app.domain.audit import AuditEntry, AuditEvent, AuditFilter, Source
from app.domain.authorization import Principal, Scope
from app.domain.booking import Booking, MeetingRef, Slot, SlotUsage
from app.domain.branding import Branding, FontFile, FontRole, Logo
from app.domain.calls import (
    BookingEvent,
    BookingOverview,
    CallStatus,
    MeetingRequest,
    WorkItem,
)
from app.domain.channel_settings import ChannelConfig, ChannelSettings
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.checks import Finding, GroupCheck
from app.domain.closure import Closure
from app.domain.contacts import ContactBlock
from app.domain.direct_queue import DirectCall
from app.domain.holidays import GroupHolidays, Holiday, HolidayCalendar
from app.domain.job_plan import JobType, PlannedJob
from app.domain.maintenance import MaintenanceState
from app.domain.retention import RetentionFacts
from app.domain.scheduling import (
    Group,
    GroupSummary,
    Organisation,
    ScheduleOverride,
    ScheduleRule,
)
from app.domain.staff import StaffFilter, StaffMember
from app.domain.updates import Release, ReleaseCheck, UpdateRequest


class Clock(Protocol):
    """Source of the current time. Injected so that time-based rules are testable."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...


@dataclass(frozen=True)
class Attachment:
    """A file attached to a message, e.g. the ``.ics`` calendar entry."""

    filename: str
    content_type: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class OutboundMessage:
    """A message to one citizen. Contains personal data: keep it in memory only."""

    channel: ChannelKind
    recipient: str = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)
    html: str | None = field(default=None, repr=False)
    attachments: tuple[Attachment, ...] = ()


class MessageSender(Protocol):
    """Delivers messages on one channel (ADR 0009): SMTP for email, an SMS gateway for SMS."""

    async def send(self, message: OutboundMessage) -> None:
        """Deliver ``message``; raise on failure so the job is retried."""
        ...


class SenderDirectory(Protocol):
    """Finds the sender of an organisation's channel (ADR 0022).

    The organisation's own settings come first, else the installation's fallback for email.
    """

    async def sender(self, organisation_id: int, channel: ChannelKind) -> MessageSender | None:
        """Return the sender, or ``None`` when the organisation has none on ``channel``."""
        ...


@dataclass(frozen=True)
class LoginStart:
    """Where to send an admin to log in, and the secrets to check the answer with.

    ``state``, ``nonce`` and ``code_verifier`` are kept in the signed session until the
    identity provider redirects back; they are never logged.
    """

    url: str
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)


class IdentityError(Exception):
    """Login failed; ``code`` says why (``token_exchange_failed``, ``invalid_token``, …)."""

    def __init__(self, code: str) -> None:
        """Store the code; it is also the message."""
        super().__init__(code)
        self.code = code


class IntegrationError(Exception):
    """A call to Webex or WxCC failed; ``code`` says why, ``retryable`` whether to try again.

    The code is a short machine key (``unauthorized``, ``rate_limited``, ``server_error``,
    ``rejected``); it never contains response bodies, which could hold personal data.
    """

    def __init__(self, code: str, *, retryable: bool) -> None:
        """Store the code and whether a retry can help."""
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class VideoProvider(Protocol):
    """Schedules and removes video meetings (Webex Meetings API, ADR 0017)."""

    async def create_meeting(self, request: MeetingRequest) -> MeetingRef:
        """Schedule a meeting and return its id and join link.

        Raises:
            IntegrationError: If the provider refuses or cannot be reached.
        """
        ...

    async def delete_meeting(self, meeting_id: str, host_email: str) -> None:
        """Delete a meeting; deleting one that no longer exists is not an error."""
        ...


class IntegrationChecker(Protocol):
    """Checks a group's integration chain at the platforms (read-only; verify button)."""

    async def check_group(self, group: Group, peers: Sequence[Group]) -> list[Finding]:
        """Return the findings for ``group``; ``peers`` are its organisation's groups.

        Never raises for a platform problem: an unreachable or refusing platform is a finding.
        """
        ...


class ContactCenterDispatcher(Protocol):
    """Creates and ends work items in Webex Contact Center (ADR 0018, 0021)."""

    async def create_task(self, item: WorkItem) -> str:
        """Create the work item and return the task id.

        Raises:
            IntegrationError: If WxCC refuses or cannot be reached.
        """
        ...

    async def end_task(self, task_id: str) -> None:
        """End the task (End Task API; needs ``cjp:user``).

        Raises:
            IntegrationError: If WxCC refuses (``rejected_403`` without the scope) or cannot be
                reached.
        """
        ...

    async def mark_cancelled(self, task_id: str, reference: str) -> None:
        """Tell the agent the booking was cancelled (Task Messages API, ``cjp:task_write``).

        Raises:
            IntegrationError: If WxCC refuses or cannot be reached.
        """
        ...


class ReleaseSource(Protocol):
    """Where the latest published release is looked up (GitHub releases, ADR 0029)."""

    async def latest(self) -> Release | None:
        """Return the latest published release; ``None`` when there is none or it is no version.

        Raises:
            IntegrationError: If the source refuses or cannot be reached.
        """
        ...


class IdentityProvider(Protocol):
    """The admin portal's identity provider (OIDC; PingFederate in production, ADR 0013)."""

    async def start_login(self, redirect_uri: str) -> LoginStart:
        """Prepare an authorisation request with fresh ``state``, ``nonce`` and PKCE."""
        ...

    async def finish_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> Principal:
        """Exchange the code, verify the ID token and return the admin it describes.

        Raises:
            IdentityError: If the exchange or any token check fails.
        """
        ...

    async def logout_url(self, post_logout_redirect_uri: str) -> str | None:
        """Return where to end the session at the provider, or ``None`` if it has no logout.

        Without this, the provider's single sign-on would log the admin straight back in.
        """
        ...


class Transaction(Protocol):
    """Everything the domain reads and writes within one database transaction.

    Every ``list_*`` method takes a ``Scope`` and returns only what it covers (ADR 0014); a
    test enforces this for new methods.
    """

    async def list_groups(self, scope: Scope) -> list[GroupSummary]:
        """Return the groups inside ``scope``, ordered by organisation and name."""
        ...

    async def save_group_check(self, check: GroupCheck) -> None:
        """Store the latest integration check of a group, replacing the previous one."""
        ...

    async def get_group_check(self, group_id: int) -> GroupCheck | None:
        """Return the latest integration check of a group, if any."""
        ...

    async def get_group_summary(self, slug: str) -> GroupSummary | None:
        """Return one group with its organisation, for an authorisation decision."""
        ...

    async def add_audit_event(self, event: AuditEvent) -> None:
        """Store an audit event."""
        ...

    async def list_audit_events(
        self, scope: Scope, limit: int = 100, narrow: AuditFilter | None = None
    ) -> list[AuditEntry]:
        """Return the newest audit events about organisations and groups inside ``scope``.

        ``narrow`` only ever narrows further; it never widens the scope. Each entry carries
        the actor's display name from their ``StaffMember`` record, if any.
        """
        ...

    async def delete_audit_events(self, source: Source, before: datetime) -> int:
        """Delete the events of ``source`` older than ``before``; return how many (ADR 0031)."""
        ...

    async def get_staff(self, subject: str) -> StaffMember | None:
        """Return the staff record of ``subject`` with all its memberships (ADR 0046)."""
        ...

    async def save_staff(self, member: StaffMember) -> None:
        """Create or replace the staff record of ``member.subject`` and its memberships."""
        ...

    async def set_staff_seen(self, subject: str, at: datetime) -> None:
        """Record the latest request of ``subject``; unknown subjects are ignored."""
        ...

    async def set_staff_logout(self, subject: str, at: datetime) -> None:
        """Record the latest logout of ``subject``; unknown subjects are ignored."""
        ...

    async def list_staff(self, scope: Scope, narrow: StaffFilter) -> list[StaffMember]:
        """Return the staff ``scope`` may see (``app.domain.staff.visible``), by name.

        ``narrow`` only ever narrows further. Each member carries all memberships; the
        caller hides those outside the scope.
        """
        ...

    async def get_group(self, slug: str) -> Group | None:
        """Return the group with ``slug``."""
        ...

    async def get_group_by_id(self, group_id: int) -> Group | None:
        """Return the group with ``group_id``."""
        ...

    async def get_schedule(
        self, group_id: int, first_day: date, last_day: date
    ) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
        """Return all rules of the group and its overrides between the two dates."""
        ...

    async def list_organisations(self, scope: Scope) -> list[Organisation]:
        """Return the organisations inside ``scope``, ordered by slug."""
        ...

    async def get_organisation(self, slug: str) -> Organisation | None:
        """Return the organisation with ``slug``, with its branding."""
        ...

    async def get_organisation_by_id(self, organisation_id: int) -> Organisation | None:
        """Return the organisation with ``organisation_id``, with its branding."""
        ...

    async def add_organisation(self, organisation: Organisation) -> Organisation:
        """Insert an organisation (default branding) and return it with its ID."""
        ...

    async def save_organisation(self, organisation: Organisation) -> None:
        """Store name and default locale; slug and branding are left alone."""
        ...

    async def save_branding(self, organisation_id: int, branding: Branding) -> None:
        """Store name, accent, theme and header style (logo and fonts have their own calls)."""
        ...

    async def get_logo(self, organisation_slug: str) -> Logo | None:
        """Return the organisation's logo, if it has one."""
        ...

    async def save_logo(self, organisation_id: int, logo: Logo | None) -> None:
        """Store or, with ``None``, remove the organisation's logo."""
        ...

    async def get_font(self, organisation_slug: str, role: FontRole) -> FontFile | None:
        """Return the organisation's font of ``role``, if it has one."""
        ...

    async def save_font(self, organisation_id: int, role: FontRole, font: FontFile | None) -> None:
        """Store or, with ``None``, remove the organisation's font of ``role``."""
        ...

    async def get_contact_block(self, organisation_id: int) -> ContactBlock | None:
        """Return the organisation's default contact block (ADR 0037), if it has one."""
        ...

    async def save_contact_block(self, organisation_id: int, block: ContactBlock | None) -> None:
        """Store or, with ``None``, remove the organisation's default contact block."""
        ...

    async def get_group_contact_block(self, group_id: int) -> ContactBlock | None:
        """Return the group's own contact block (ADR 0037 §2), if it has one."""
        ...

    async def save_group_contact_block(self, group_id: int, block: ContactBlock | None) -> None:
        """Store or, with ``None``, remove the group's own contact block."""
        ...

    async def add_group(self, group: Group) -> Group:
        """Store a new group and return it with its id; the slug must be free."""
        ...

    async def save_group(self, group: Group) -> None:
        """Store the changed settings of an existing group (its slug never changes)."""
        ...

    async def count_future_bookings(self, group_id: int, now: datetime) -> int:
        """Return how many confirmed bookings of the group start after ``now``."""
        ...

    async def get_schedule_items(
        self, group_id: int, from_day: date | None = None
    ) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
        """Return the group's rules and overrides (from ``from_day`` on), each with its id."""
        ...

    async def add_schedule_rule(self, group_id: int, rule: ScheduleRule) -> int:
        """Store a rule (window with its agent count and label) for the group; return its id."""
        ...

    async def update_schedule_rule(self, group_id: int, rule_id: int, rule: ScheduleRule) -> bool:
        """Replace the group's rule ``rule_id``; ``False`` if the group has no such rule."""
        ...

    async def delete_schedule_rule(self, group_id: int, rule_id: int) -> bool:
        """Delete the group's rule ``rule_id``; ``False`` if the group has no such rule."""
        ...

    async def add_schedule_override(self, group_id: int, override: ScheduleOverride) -> int:
        """Store an override for the group and return its id."""
        ...

    async def delete_schedule_override(self, group_id: int, override_id: int) -> bool:
        """Delete the group's override ``override_id``; ``False`` if it has no such override."""
        ...

    async def get_holiday_calendars(self) -> list[HolidayCalendar]:
        """Return every holiday calendar, by name.

        Calendars are global reference data (``holidays.manage`` is a global permission,
        ADR 0025): every admin who chooses calendars sees all of them, so there is no scope.
        """
        ...

    async def get_holiday_calendar(self, slug: str) -> HolidayCalendar | None:
        """Return the holiday calendar with ``slug``."""
        ...

    async def add_holiday_calendar(self, calendar: HolidayCalendar) -> HolidayCalendar:
        """Store a new calendar and return it with its id; the slug must be free."""
        ...

    async def delete_holiday_calendar(self, calendar_id: int) -> None:
        """Delete the calendar and its holidays (the caller checks it is not in use)."""
        ...

    async def count_holiday_calendar_users(self, calendar_id: int) -> int:
        """Return how many organisations and groups chose the calendar."""
        ...

    async def get_holidays(
        self, calendar_ids: Collection[int], first_day: date, last_day: date
    ) -> list[Holiday]:
        """Return the holidays of the calendars between the two dates, by date."""
        ...

    async def add_holiday(self, holiday: Holiday) -> int:
        """Store a holiday and return its id; its calendar has no holiday on that date yet."""
        ...

    async def delete_holiday(self, calendar_id: int, holiday_id: int) -> bool:
        """Delete the calendar's holiday; ``False`` if the calendar has no such holiday."""
        ...

    async def get_organisation_holiday_calendars(self, organisation_id: int) -> frozenset[int]:
        """Return the ids of the calendars the organisation chose."""
        ...

    async def save_organisation_holiday_calendars(
        self, organisation_id: int, calendar_ids: frozenset[int]
    ) -> None:
        """Replace the organisation's calendar choice."""
        ...

    async def get_group_holidays(self, group_id: int) -> GroupHolidays:
        """Return the group's holiday choice (inherit, own calendars, days kept open)."""
        ...

    async def save_group_holidays(self, group_id: int, holidays: GroupHolidays) -> None:
        """Replace the group's holiday choice."""
        ...

    async def save_meeting(self, booking_id: int, meeting: MeetingRef) -> None:
        """Store the booking's meeting id and join link."""
        ...

    async def save_task_id(self, booking_id: int, task_id: str) -> None:
        """Store the booking's WxCC task id."""
        ...

    async def get_booking_by_meeting(self, meeting_id: str) -> Booking | None:
        """Return the booking whose meeting has ``meeting_id``."""
        ...

    async def get_booking_by_task(self, task_id: str) -> Booking | None:
        """Return the booking whose WxCC task has ``task_id``."""
        ...

    async def claim_webhook_event(self, source: str, key: str, at: datetime) -> bool:
        """Remember that the event ``key`` from ``source`` was seen; ``False`` if it was before."""
        ...

    async def forget_webhook_events(self, before: datetime) -> int:
        """Forget event keys seen before ``before``; return how many."""
        ...

    async def add_booking_event(self, event: BookingEvent) -> None:
        """Append an event to a booking's timeline."""
        ...

    async def get_booking_events(self, booking_id: int) -> list[BookingEvent]:
        """Return a booking's timeline, oldest first (access is checked by the caller)."""
        ...

    async def list_bookings(
        self, scope: Scope, group_id: int, lower: datetime, upper: datetime
    ) -> list[BookingOverview]:
        """Return the group's bookings with lower <= start < upper and their call status.

        The group must lie inside ``scope`` (ADR 0014); otherwise the list is empty.
        """
        ...

    async def get_call_status(self, booking_id: int) -> CallStatus | None:
        """Return the booking's aggregated call status, if anything happened yet."""
        ...

    async def save_call_status(self, status: CallStatus) -> None:
        """Create or replace the booking's aggregated call status."""
        ...

    async def get_direct_calls(self, group_id: int, since: datetime) -> list[DirectCall]:
        """Return the group's direct calls that started at or after ``since``, any status."""
        ...

    async def get_reminder_rules(self, group_id: int) -> list[ReminderRule]:
        """Return the group's reminder rules."""
        ...

    async def add_reminder_rule(self, group_id: int, rule: ReminderRule) -> None:
        """Store a reminder rule; its channel and offset must be new for the group."""
        ...

    async def delete_reminder_rule(
        self, group_id: int, channel: ChannelKind, minutes_before: int
    ) -> bool:
        """Delete the group's rule; ``False`` if the group has no such rule."""
        ...

    async def get_channel_settings(self, organisation_id: int) -> list[ChannelSettings]:
        """Return the organisation's configured channels (never a secret value)."""
        ...

    async def save_channel_settings(
        self, organisation_id: int, config: ChannelConfig, secret: str | None, now: datetime
    ) -> None:
        """Create or replace the channel's settings.

        ``secret=None`` keeps the stored secret; a value replaces it, encrypted (ADR 0008).
        """
        ...

    async def delete_channel_settings(self, organisation_id: int, channel: ChannelKind) -> bool:
        """Remove the channel's settings and secret; ``False`` if none were stored."""
        ...

    async def get_group_texts(self, group_id: int) -> dict[tuple[str, str], str]:
        """Return the group's own message texts by ``(locale, block key)``."""
        ...

    async def save_group_text(self, group_id: int, locale: str, key: str, text: str | None) -> None:
        """Store the group's text of a block in a locale; ``None`` removes it."""
        ...

    async def get_slot_usage(
        self, group_id: int, lower: datetime, upper: datetime
    ) -> list[SlotUsage]:
        """Return capacity and active bookings of existing slots with lower <= start < upper."""
        ...

    async def get_slot(self, group_id: int, start: datetime) -> Slot | None:
        """Return the existing slot of the group at ``start``."""
        ...

    async def add_slot(self, slot: Slot) -> Slot:
        """Store a new slot and return it with its ID."""
        ...

    async def count_active_bookings(self, slot_id: int) -> int:
        """Return the number of confirmed bookings in the slot."""
        ...

    async def add_booking(self, booking: Booking) -> Booking:
        """Store a new booking and return it with its ID."""
        ...

    async def get_booking(self, booking_id: int) -> Booking | None:
        """Return the booking with ``booking_id``."""
        ...

    async def purge_personal_data(self, booking_id: int, now: datetime) -> None:
        """Set every PII column of the booking to NULL and record ``pii_purged_at``."""
        ...

    async def get_booking_by_token_hash(self, token_hash: str) -> Booking | None:
        """Return the booking whose management token hashes to ``token_hash``."""
        ...

    async def list_retention(self, scope: Scope, candidates_before: datetime) -> RetentionFacts:
        """Return the retention counts per group in ``scope`` and the possibly overdue bookings.

        Counts, statuses and times only, never personal data or booking ids (ADR 0039).
        Candidates still hold personal data and ended or were cancelled before
        ``candidates_before``, or are suspended.
        """
        ...

    async def save_booking(self, booking: Booking) -> None:
        """Persist changes to an existing booking (status, cancelled_at)."""
        ...

    async def replace_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        """Delete the booking's pending jobs and store ``jobs`` instead (CLAUDE.md)."""
        ...

    async def add_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        """Store ``jobs`` next to the booking's pending jobs."""
        ...

    async def delete_pending_jobs(self, booking_id: int, types: Collection[JobType]) -> None:
        """Delete the booking's pending jobs of the given types."""
        ...

    async def get_shortfall_acceptances(
        self, group_id: int, lower: datetime, upper: datetime
    ) -> list[ShortfallAcceptance]:
        """Return the group's accepted shortfalls of slots starting in [lower, upper)."""
        ...

    async def save_shortfall_acceptance(self, acceptance: ShortfallAcceptance) -> None:
        """Store a planner's acceptance; it replaces an earlier one for the same slot."""
        ...

    async def count_audit_events(
        self, actor: str, actions: Collection[str], since: datetime
    ) -> int:
        """Return how many of ``actor``'s events with one of ``actions`` happened since then."""
        ...

    async def get_closures(self, group_id: int, now: datetime) -> list[Closure]:
        """Return the group's closures that have not ended at ``now``, earliest first."""
        ...

    async def get_closure(self, closure_id: int) -> Closure | None:
        """Return the closure with ``closure_id``."""
        ...

    async def add_closure(self, closure: Closure) -> Closure:
        """Store a new closure with its notices and return it with its id."""
        ...

    async def save_closure(self, closure: Closure) -> None:
        """Store the end and the notices of an existing closure."""
        ...

    async def find_confirmed_bookings(
        self, group_id: int, lower: datetime, upper: datetime | None
    ) -> list[Booking]:
        """Return the group's confirmed scheduled bookings with lower <= start < upper.

        ``upper=None`` means no upper bound. For the domain's own use, not for listings.
        """
        ...

    async def list_suspended_bookings(self, scope: Scope, group_id: int) -> list[Booking]:
        """Return the group's suspended bookings, earliest first.

        The group must lie inside ``scope`` (ADR 0014); otherwise the list is empty.
        """
        ...

    async def get_api_settings(self) -> ApiSettings:
        """Return the machine API's switches (both off when never saved, ADR 0023)."""
        ...

    async def save_api_settings(self, settings: ApiSettings) -> None:
        """Store the machine API's switches."""
        ...

    async def list_api_clients(self, scope: Scope) -> list[ApiClient]:
        """Return the API clients of the organisations inside ``scope``, ordered by name."""
        ...

    async def get_api_client(self, client_id: str) -> ApiClient | None:
        """Return the API client with the public ``client_id``."""
        ...

    async def add_api_client(self, client: ApiClient) -> ApiClient:
        """Store a new API client and return it with its id."""
        ...

    async def save_api_client(self, client: ApiClient) -> None:
        """Store the changed fields of an existing API client (its organisation never changes)."""
        ...

    async def add_access_token(self, token: AccessToken) -> None:
        """Store an issued access token (its hash only)."""
        ...

    async def get_access_token(self, token_hash: str) -> AccessToken | None:
        """Return the stored access token with ``token_hash``."""
        ...

    async def revoke_access_tokens(self, client_id: str) -> int:
        """Delete every access token of the client; return how many were live."""
        ...

    async def prune_access_tokens(self, client_id: str, now: datetime, keep: int) -> None:
        """Delete expired tokens of every client, and all but the newest ``keep`` of this one."""
        ...

    async def get_release_check(self) -> ReleaseCheck | None:
        """Return the outcome of the latest release check (ADR 0029), if there was one."""
        ...

    async def save_release_check(self, check: ReleaseCheck) -> None:
        """Replace the outcome of the latest release check."""
        ...

    async def get_update_requests(self, limit: int = 20) -> list[UpdateRequest]:
        """Return the newest update requests, newest first.

        Updates concern the installation, not an organisation or group (``system.update`` is
        a global permission), so there is no scope.
        """
        ...

    async def add_update_request(self, request: UpdateRequest) -> UpdateRequest:
        """Store a new request and return it with its id."""
        ...

    async def save_update_request(self, request: UpdateRequest) -> None:
        """Store the status, times and reason of an existing request."""
        ...

    async def get_maintenance_state(self) -> MaintenanceState:
        """Return the maintenance switch of the installation (ADR 0038); off if never set."""
        ...

    async def save_maintenance_state(self, state: MaintenanceState) -> None:
        """Replace the maintenance switch."""
        ...


class UnitOfWork(Protocol):
    """Opens transactions. Leaving the context commits; an exception rolls back."""

    def read(self) -> AbstractAsyncContextManager[Transaction]:
        """Open a read transaction; it never blocks or is blocked by writers."""
        ...

    def locked(self) -> AbstractAsyncContextManager[Transaction]:
        """Open a write transaction holding the database write lock from the start.

        On SQLite this is ``BEGIN IMMEDIATE``: two bookings can never both see the last
        free place. Raises ``DomainError(database_busy)`` if the lock cannot be obtained.
        """
        ...
