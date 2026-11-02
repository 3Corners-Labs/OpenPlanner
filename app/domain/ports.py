"""Ports: Protocols for everything outside the domain.

Adapters in ``app.adapters`` implement them; tests use the fakes in ``tests/fakes``.
External systems still to come with their work package: ``VideoProvider`` (WP5, including
live meeting status per ADR 0010), ``ContactCenterDispatcher`` (WP6, ADR 0007),
``IdentityProvider`` (WP7).
"""

from collections.abc import Collection, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from app.domain.admin_sessions import AdminSession, EndReason
from app.domain.agenda import ShortfallAcceptance
from app.domain.api_clients import AccessToken, ApiClient, ApiSettings
from app.domain.audit import AuditEntry, AuditEvent, AuditFilter, Category
from app.domain.authorization import CustomRole, Scope
from app.domain.booking import (
    Booking,
    BookingStatus,
    Claim,
    MeetingRef,
    Slot,
    SlotUsage,
    TrialCall,
)
from app.domain.branding import Branding, FontFile, FontRole, Logo
from app.domain.calls import (
    BookingEvent,
    BookingOverview,
    CallStatus,
    EventKind,
    MeetingRequest,
    MeetingStatus,
    TaskSnapshot,
    WorkItem,
)
from app.domain.channel_settings import ChannelConfig, ChannelSettings
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.checks import Finding, GroupCheck
from app.domain.closure import Closure
from app.domain.configuration_package import PackageSummary
from app.domain.contacts import ContactBlock
from app.domain.diagnostic_mode import DiagnosticMode
from app.domain.direct_queue import DirectCall
from app.domain.embedding import AllowedOrigins
from app.domain.entitlements import Directory, LoginClaims
from app.domain.form_fields import FormDefinition
from app.domain.holidays import GroupHolidays, Holiday, HolidayCalendar
from app.domain.identity_providers import GeneratedKey, OrganisationProvider, SigningKey
from app.domain.images import ImageFile, ImageText
from app.domain.job_plan import JobCounts, JobSummary, JobType, PlannedJob
from app.domain.log_destinations import DestinationStatus, LogDestination
from app.domain.maintenance import MaintenanceState
from app.domain.own_certificate import HostKeys, OwnCertificate, ParsedChain, ParsedKey
from app.domain.participant_links import LinkSession
from app.domain.retention import DueBooking, RetentionFacts
from app.domain.scenarios import ClosureScenario
from app.domain.scheduling import (
    Group,
    GroupSummary,
    Organisation,
    ScheduleOverride,
    ScheduleRule,
)
from app.domain.staff import GrantHolders, RefusedEntitlement, StaffFilter, StaffMember
from app.domain.tasks import ActiveJobs
from app.domain.tls import CertificateCheck, ProbeResult
from app.domain.updates import ListedRelease, ReleaseCheck, RunningVersion, UpdateRequest
from app.domain.worker_check import WorkerHeartbeat


class EventSink(Protocol):
    """Ships committed audit events to the log platform (ADR 0034).

    Called once per committed transaction with the events it wrote, never after a rollback.
    """

    def emit(self, events: Sequence[AuditEvent]) -> None:
        """Hand the events on; must not raise, a failure is only logged."""
        ...


class LogDestinationSender(Protocol):
    """Delivers batches of log lines to one log destination (ADR 0085).

    One adapter per ``DestinationKind``, built from a destination and its opened credential.
    """

    async def send(self, lines: Sequence[str]) -> None:
        """Deliver the lines in order, each one JSON object, already masked.

        Raises:
            DeliveryError: With a machine reason; the caller keeps the batch and retries.
        """
        ...

    async def aclose(self) -> None:
        """Release the connection."""
        ...


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
    """A message to one participant. Contains personal data: keep it in memory only."""

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


@dataclass(frozen=True)
class LoginTestReport:
    """What a guided test login through an organisation provider showed (ADR 0083 point 9).

    Only names and verdicts: the claim names that arrived, whether ``email_verified`` was
    true, the domain of the ``email`` claim (never the address), and the claims as the login
    would read them. The superuser sees it; no session is started from it.

    Attributes:
        claims: The claims as ``finish_login`` would return them.
        claim_names: The names of the claims in the ID token, sorted and bounded.
        email_verified: The ``email_verified`` claim, ``None`` when absent.
        email_domain: The part after ``@`` of the ``email`` claim, ``None`` when absent.
        mfa_shown: Whether the token showed a second factor under the provider's accepted
            ``amr``/``acr`` values (ADR 0100).
    """

    claims: LoginClaims
    claim_names: tuple[str, ...]
    email_verified: bool | None
    email_domain: str | None
    mfa_shown: bool = False


class DiscoveryFetcher(Protocol):
    """Fetches a provider's discovery document through the outbound guard (ADR 0083 point 8)."""

    async def fetch(self, url: str) -> Mapping[str, object]:
        """Return the JSON document at ``url``.

        Raises:
            IdentityError: ``discovery_failed`` when it cannot be fetched or is no JSON object,
                or the guard's reason (``not_https``, ``not_public``, ``redirect``, …).
        """
        ...


class SigningKeys(Protocol):
    """Generates the key pair a provider signs its client assertions with (point 6)."""

    def generate(self) -> GeneratedKey:
        """Return a fresh EC P-256 key pair with a unique key id."""
        ...


REASON_LIMIT = 300
"""Longest provider reason kept on an error or an event (the column size, ADR 0065)."""


class IntegrationError(Exception):
    """A call to Webex or WxCC failed; ``code`` says why, ``retryable`` whether to try again.

    The code is a short machine key (``unauthorized``, ``rate_limited``, ``server_error``,
    ``rejected``); it never contains response bodies, which could hold personal data.
    ``reason`` is the provider's own explanation (ADR 0065): set only by adapters for requests
    that carry no participant data, with addresses masked and cut at ``REASON_LIMIT``; it is kept
    with the failure on the timeline and the system event, so admins read it on the page.
    """

    def __init__(self, code: str, *, retryable: bool, reason: str = "") -> None:
        """Store the code, whether a retry can help, and the provider's reason, if any."""
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.reason = reason[:REASON_LIMIT]


@dataclass(frozen=True)
class RequestRecord:
    """One HTTP request an adapter made to a platform (ADR 0068): codes and numbers only.

    Attributes:
        method: ``POST``, ``DELETE``, ...
        path: The URL path without its query string (a query may carry an address).
        status: The HTTP status, or ``0`` when the platform could not be reached.
        error: The ``IntegrationError`` code the request ended in; empty on success.
        tracking_id: The platform's id of the request (Webex ``trackingId``), for its support.
        duration_ms: How long the request took.
    """

    method: str
    path: str
    status: int
    error: str = ""
    tracking_id: str = ""
    duration_ms: int = 0


class RequestLog(Protocol):
    """Where an adapter reports every request it made (ADR 0068); never raises to the caller."""

    async def record(self, record: RequestRecord) -> None:
        """Keep ``record`` for the booking page and the audit log's chain view."""
        ...


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

    async def meeting_status(self, meeting_id: str, host_email: str) -> MeetingStatus:
        """Return the meeting's state, participant count and whether the host joined (ADR 0010).

        The polling fallback for webhooks that did not arrive. Aggregates only: names and
        addresses of participants never leave the adapter.

        Raises:
            IntegrationError: If the provider refuses or cannot be reached.
        """
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

    async def read_task(self, task_id: str) -> TaskSnapshot:
        """Return the task's state as the platform has it (ADR 0082 point 1).

        Only the state, the platform's status word and the time of the last change come back,
        never the task's attributes. A task the platform does not know is ``TaskState.NONE``.

        Raises:
            IntegrationError: If WxCC refuses or cannot be reached.
        """
        ...


class ProbeRegistry(Protocol):
    """Remembers the probe work item of a group check and the events it got (ADR 0082).

    The group check creates a work item with kind ``probe`` and waits for its ``task:new``
    and ``task:ended`` events on the webhook. The webhook handler asks the registry whether
    an unknown task id is the open probe and notes the event; the check polls the events.
    """

    async def open(self, task_id: str, at: datetime) -> None:
        """Start waiting for the events of probe ``task_id``; replaces an earlier probe."""
        ...

    async def note(self, task_id: str, kind: EventKind, at: datetime) -> bool:
        """Note an event of ``task_id`` if it is the open probe; return whether it was."""
        ...

    async def events(self, task_id: str) -> tuple[EventKind, ...]:
        """Return the events noted for ``task_id``, in order; empty for another task."""
        ...


class ReleaseSource(Protocol):
    """Where the published releases are looked up (GitHub releases, ADR 0029, 0067)."""

    async def listing(self) -> list[ListedRelease]:
        """Return the newest published releases and candidates with their notes.

        Drafts and anything that is no version are left out. Empty when there is none.

        Raises:
            IntegrationError: If the source refuses or cannot be reached.
        """
        ...


class CertificateProbe(Protocol):
    """Performs a TLS handshake with a server and reads the certificate it presents (ADR 0064)."""

    async def probe(self, host: str, port: int) -> ProbeResult:
        """Connect to ``host:port``, verify the chain against the host's CA bundle, read the leaf.

        Never raises: a failed connection or handshake is a result with an error code.
        """
        ...


class MailDomainResolver(Protocol):
    """Looks up the TXT records of a DNS name, for the sender domain check (ADR 0073)."""

    async def txt_records(self, name: str) -> tuple[str, ...] | None:
        """Return the TXT records of ``name`` (``()`` when it has none).

        Returns ``None`` when the lookup failed (timeout, no resolver); never raises.
        """
        ...


class PemReader(Protocol):
    """Parses an uploaded certificate chain or private key (ADR 0070)."""

    def read_chain(self, chain: bytes) -> ParsedChain:
        """Return the leaf's public data, fingerprint, public key and the normalised chain.

        Raises:
            DomainError: ``tls_certificate_unreadable`` (field ``chain``).
        """
        ...

    def read_key(self, key: bytes) -> ParsedKey:
        """Return the normalised key and its public key; the caller hands it to the volume.

        Raises:
            DomainError: ``tls_certificate_unreadable`` (field ``key``).
        """
        ...


class CertificateVolume(Protocol):
    """The directory shared with the proxy: the key lives here and nowhere else (ADR 0070).

    ``privkey.pem`` is the key the proxy serves with, ``pending.key``/``pending.csr`` a key
    waiting for its certificate, ``fullchain.pem`` and ``serial`` the served pair. Reading
    and publishing never raise; making or importing a key raises when the volume cannot be
    written, because the superuser must hear that.
    """

    def keys(self) -> HostKeys:
        """Return the public keys on this host and the pending request."""
        ...

    def generate_pending(self, host: str) -> str:
        """Make a new key and a signing request for ``host``; return the request (PEM).

        Raises:
            DomainError: ``tls_volume_unwritable``.
        """
        ...

    def promote_pending(self) -> None:
        """Make the pending key the served one (its chain was uploaded)."""
        ...

    def import_key(self, key_pem: str) -> None:
        """Write an imported key as the served one, dropping any pending request.

        Raises:
            DomainError: ``tls_volume_unwritable``.
        """
        ...

    def publish(self, chain_pem: str, fingerprint: str) -> None:
        """Write the chain and, last, the serial, so the proxy serves the pair."""
        ...

    def withdraw(self) -> None:
        """Remove the serial and the chain, so the proxy falls back to ACME; the key stays."""
        ...


class ConfigurationTransfer(Protocol):
    """Reads and writes configuration packages (ADR 0089): SQLite files, secrets re-sealed.

    The adapter opens the installation's sealed values with its own key and seals them under
    the package's passphrase on export, and the other way round on import. It never logs or
    returns a passphrase or an opened value.
    """

    async def export(self, passphrase: str) -> bytes:
        """Return a package of this installation sealed under ``passphrase``.

        Raises:
            DomainError: ``integration_not_ready`` without a settings key.
        """
        ...

    async def inspect(self, package: bytes, passphrase: str) -> PackageSummary:
        """Return what ``package`` holds, once ``passphrase`` opens it.

        Raises:
            DomainError: ``configuration_package_invalid`` for anything that is not a package
                of the known format; ``configuration_passphrase_wrong``.
        """
        ...

    async def import_(self, package: bytes, passphrase: str) -> PackageSummary:
        """Replace every configuration table with the package's; return its summary.

        Raises:
            DomainError: The codes of ``inspect``; ``configuration_package_version`` when the
                schema revisions differ; ``integration_not_ready`` without a settings key.
        """
        ...

    async def in_use(self) -> bool:
        """Return whether the installation has bookings, slots or an active closure."""
        ...


class IdentityProvider(Protocol):
    """The admin portal's identity provider (OIDC; PingFederate in production, ADR 0013)."""

    async def start_login(self, redirect_uri: str, *, fresh: bool = False) -> LoginStart:
        """Prepare an authorisation request with fresh ``state``, ``nonce`` and PKCE.

        ``fresh`` asks the provider to authenticate again instead of single sign-on (the dry
        run of a new login mapping, ADR 0042 §8).
        """
        ...

    async def finish_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> LoginClaims:
        """Exchange the code, verify the ID token and return what it says about the admin.

        The claims are extracted, not interpreted: ``app.domain.entitlements.resolve`` turns
        them into a ``Principal`` with the directory (ADR 0049).

        Raises:
            IdentityError: If the exchange or any token check fails.
        """
        ...

    async def finish_trial_login(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce: str,
        trial: Mapping[str, str],
    ) -> tuple[LoginClaims, LoginClaims]:
        """Like ``finish_login``, and also extract the same claims with the ``trial`` mapping.

        Returns what the current mapping and the proposed one make of the ID token; the
        token itself is never returned or stored.

        Raises:
            IdentityError: If the exchange or any token check fails.
        """
        ...

    async def finish_test_login(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> LoginTestReport:
        """Like ``finish_login``, and also report which claims arrived (ADR 0083 point 9).

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

    async def list_groups(self, scope: Scope, *, archived: bool = False) -> list[GroupSummary]:
        """Return the groups inside ``scope``, ordered by organisation and name.

        ``archived`` lists the archived groups instead of the ones in use (ADR 0084).
        """
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

    async def delete_audit_events(
        self, category: Category, before: datetime, action: str | None = None
    ) -> int:
        """Delete the events of ``category`` older than ``before``; return how many (ADR 0040).

        With ``action`` only the events with that action go (the request events, ADR 0068).
        """
        ...

    async def active_diagnostic_modes(self, now: datetime) -> list[DiagnosticMode]:
        """Return the diagnostic modes that have not ended at ``now`` (ADR 0040)."""
        ...

    async def save_diagnostic_mode(self, mode: DiagnosticMode) -> None:
        """Start ``mode``, replacing any mode for the same target."""
        ...

    async def end_diagnostic_mode(
        self, organisation: str, group: str | None, now: datetime
    ) -> bool:
        """End the mode of one target; return whether one was active."""
        ...

    async def end_all_diagnostic_modes(self, now: datetime) -> int:
        """End every active mode; return how many."""
        ...

    async def get_staff(self, subject: str) -> StaffMember | None:
        """Return the staff record of ``subject`` with all its memberships (ADR 0046)."""
        ...

    async def save_staff(self, member: StaffMember) -> None:
        """Create or replace the staff record of ``member.subject`` and its memberships."""
        ...

    async def add_admin_session(self, session: AdminSession) -> None:
        """Store a new admin session (ADR 0098); only the hash of its reference is kept."""
        ...

    async def get_admin_session(self, id_hash: str) -> AdminSession | None:
        """Return the session with this reference hash, ended or not, or ``None``."""
        ...

    async def active_admin_sessions_of(self, subject: str) -> list[AdminSession]:
        """Return the sessions of ``subject`` that have not ended, oldest ``last_seen_at`` first."""
        ...

    async def end_admin_session(self, id_hash: str, reason: EndReason, at: datetime) -> bool:
        """End a live session; return whether one was ended (unknown or ended: ``False``)."""
        ...

    async def touch_admin_session(self, id_hash: str, at: datetime) -> None:
        """Write the latest activity of a live session."""
        ...

    async def end_all_admin_sessions(self, reason: EndReason, at: datetime) -> int:
        """End every live session with ``reason``; return how many (ADR 0098 point 9c)."""
        ...

    async def delete_admin_sessions(self, login_before: datetime) -> int:
        """Delete the sessions that ended or whose login lies before ``login_before``.

        Return how many rows went (ADR 0098 point 11).
        """
        ...

    async def get_booking_id_by_public_ref(self, public_ref: str) -> int | None:
        """Return the id of the booking named by ``public_ref`` (ADR 0093), or ``None``."""
        ...

    async def add_link_session(self, value_hash: str, now: datetime, expires_at: datetime) -> int:
        """Store a new participant link session (only the hash of its value) and return its id."""
        ...

    async def get_link_session(self, value_hash: str) -> LinkSession | None:
        """Return the session with this value hash, lapsed or not, with its bookings."""
        ...

    async def touch_link_session(
        self, session_id: int, now: datetime, expires_at: datetime
    ) -> None:
        """Record a use of the session and push its end to ``expires_at``."""
        ...

    async def add_booking_to_link_session(
        self, session_id: int, booking_id: int, now: datetime
    ) -> None:
        """Let the session hold the booking (no effect when it already does)."""
        ...

    async def remove_booking_from_link_session(self, session_id: int, booking_id: int) -> None:
        """Take the booking out of the session."""
        ...

    async def delete_link_session(self, session_id: int) -> None:
        """Delete one session and the list of its bookings."""
        ...

    async def delete_lapsed_link_sessions(self, now: datetime) -> int:
        """Delete the sessions that lapsed before ``now``; return how many went."""
        ...

    async def set_staff_seen(self, subject: str, at: datetime) -> None:
        """Record the latest request of ``subject``; unknown subjects are ignored."""
        ...

    async def set_staff_logout(self, subject: str, at: datetime) -> None:
        """Record the latest logout of ``subject``; unknown subjects are ignored."""
        ...

    async def list_staff(self, scope: Scope, narrow: StaffFilter) -> list[StaffMember]:
        """Return the staff ``scope`` may see (``app.domain.staff.visible``), by name.

        ``narrow`` only ever narrows further. Each member carries all memberships and
        refusals; the caller hides those outside the scope.
        """
        ...

    async def get_directory(self) -> Directory:
        """Return the role keys, organisations and groups a claim value can name (ADR 0049)."""
        ...

    async def list_grant_holders(self, scope: Scope) -> list[GrantHolders]:
        """Return, per grant on an organisation in ``scope``, how many people last held it."""
        ...

    async def list_refused_entitlements(
        self, scope: Scope, since: datetime
    ) -> list[RefusedEntitlement]:
        """Return the refused values received since ``since``, newest first.

        Only those naming an organisation in ``scope``; every one for the superuser's scope.
        """
        ...

    async def get_group(self, slug: str) -> Group | None:
        """Return the group with ``slug``."""
        ...

    async def get_group_by_id(self, group_id: int) -> Group | None:
        """Return the group with ``group_id``."""
        ...

    async def get_form(self, group_id: int) -> FormDefinition:
        """Return the custom fields of the group's booking form, active and retired (ADR 0055).

        A group without custom fields has an empty form at version 1.
        """
        ...

    async def save_form(self, group_id: int, form: FormDefinition) -> None:
        """Store the group's form as a whole: its version, active and retired fields in order."""
        ...

    async def raise_answers_to_pii(self, group_id: int, key: str) -> int:
        """Make field ``key`` count as PII in every stored answer of the group; return how many.

        Called when a field is raised to ``pii`` or retired (ADR 0055 point 4).
        """
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

    async def count_groups(self, organisation_id: int) -> int:
        """Return how many groups the organisation has, archived ones included (ADR 0084)."""
        ...

    async def count_api_clients(self, organisation_id: int) -> int:
        """Return how many API clients belong to the organisation (ADR 0084)."""
        ...

    async def delete_organisation(self, organisation_id: int) -> None:
        """Erase the organisation's row; branding, channels, texts and settings go with it."""
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

    async def get_allowed_origins(self, organisation_id: int) -> AllowedOrigins:
        """Return the websites allowed to embed the organisation's groups (ADR 0035)."""
        ...

    async def save_allowed_origins(self, organisation_id: int, allowed: AllowedOrigins) -> None:
        """Replace the organisation's allowed origins; an empty list removes them all."""
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

    async def erasable_groups(self, archived_before: datetime) -> list[Group]:
        """Return the archived groups the worker may erase (ADR 0084 step 3).

        Archived at or before ``archived_before``, and referred to by no booking, slot,
        closure, shortfall acceptance or API client any more.
        """
        ...

    async def delete_group(self, group_id: int) -> None:
        """Erase the group's row; its settings, texts, staffing and rules go with it."""
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

    async def update_schedule_override(
        self, group_id: int, override_id: int, override: ScheduleOverride
    ) -> bool:
        """Replace the group's exception ``override_id`` (ADR 0084 step 5); ``False`` if unknown."""
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

    async def save_holiday_calendar(self, calendar: HolidayCalendar) -> None:
        """Store the name of an existing calendar; the slug never changes (ADR 0084 step 5)."""
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

    async def save_trial_call(self, booking_id: int, trial: TrialCall) -> None:
        """Store a new trial call (ADR 0071) and add one to the booking's count."""
        ...

    async def clear_trial_call(self, booking_id: int) -> None:
        """Forget the trial call's meeting and link; the count stays."""
        ...

    async def save_task_id(self, booking_id: int, task_id: str) -> None:
        """Store the booking's WxCC task id."""
        ...

    async def get_booking_by_meeting(self, meeting_id: str) -> Booking | None:
        """Return the booking whose meeting has ``meeting_id``."""
        ...

    async def get_booking_by_trial_meeting(self, meeting_id: str) -> Booking | None:
        """Return the booking whose open trial call (ADR 0071) is the meeting ``meeting_id``."""
        ...

    async def get_booking_by_task(self, task_id: str) -> Booking | None:
        """Return the booking whose WxCC task has ``task_id``."""
        ...

    async def claim_booking(self, booking_id: int, claim: Claim) -> bool:
        """Record ``claim`` if the booking is confirmed and unclaimed (ADR 0043).

        Returns ``False`` when another agent claimed it first or it is no longer confirmed.
        Must run in a locked transaction.
        """
        ...

    async def release_claim(self, booking_id: int) -> None:
        """Clear the booking's claim, so it returns to the own queue (ADR 0043)."""
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

    async def update_reminder_rule(
        self, group_id: int, channel: ChannelKind, minutes_before: int, rule: ReminderRule
    ) -> bool:
        """Replace the group's rule with this channel and offset (ADR 0084 step 5)."""
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

    async def get_organisation_texts(self, organisation_id: int) -> dict[tuple[str, str], str]:
        """Return the organisation's own texts by ``(locale, key)`` (ADR 0004)."""
        ...

    async def save_organisation_text(
        self, organisation_id: int, locale: str, key: str, text: str | None
    ) -> None:
        """Store the organisation's text of a key in a locale; ``None`` removes it."""
        ...

    async def get_all_organisation_texts(self) -> dict[tuple[int, str, str], str]:
        """Return every organisation's texts by ``(organisation id, locale, key)``."""
        ...

    async def get_all_group_terms(self) -> dict[tuple[int, str, str], str]:
        """Return every group's own agent term words by ``(group id, locale, key)`` (ADR 0077)."""
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

    async def upcoming_starts_for_email(
        self, group_id: int, email: str, after: datetime
    ) -> list[datetime]:
        """Return the starts of the group's scheduled bookings for ``email`` (ADR 0066).

        Only bookings that hold a place and end after ``after`` count. The address is
        compared without regard to (ASCII) case; only start times leave the adapter.
        """
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

    async def get_booking_by_public_ref(self, public_ref: str) -> Booking | None:
        """Return the booking named by ``public_ref`` (ADR 0093), or ``None``."""
        ...

    async def get_booking_by_token_hash(self, token_hash: str) -> Booking | None:
        """Return the booking whose management token hashes to ``token_hash``."""
        ...

    async def due_for_purge(self, candidates_before: datetime) -> list[DueBooking]:
        """Return the bookings of every group that may be overdue for a purge, with their ids.

        For the nightly sweep (system, no scope). Same selection as ``list_retention``.
        """
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

    async def claim_booking_job(self, booking_id: int, job_type: JobType, now: datetime) -> bool:
        """Take the booking's pending job of ``job_type`` for the caller, as the worker would.

        Returns whether it was taken; ``False`` when the job is gone, done, or already claimed
        (by the worker), so only one side makes the external resource.
        """
        ...

    async def finish_booking_job(self, booking_id: int, job_type: JobType, *, done: bool) -> None:
        """Settle a job taken with ``claim_booking_job``: done, or back to pending for a retry."""
        ...

    async def active_jobs(self, limit: int) -> ActiveJobs:
        """Return the first ``limit`` running or failed jobs and how many there are in all.

        Running first, newest first; never a payload or a booking (ADR 0061 §2).
        """
        ...

    async def get_booking_jobs(self, booking_id: int) -> list[JobSummary]:
        """Return the booking's jobs, oldest first, for its page (ADR 0068)."""
        ...

    async def count_group_jobs(self, group_id: int, overdue_before: datetime) -> JobCounts:
        """Count the group's failed jobs and its pending jobs planned before ``overdue_before``."""
        ...

    async def save_worker_heartbeat(self, heartbeat: WorkerHeartbeat) -> None:
        """Store the worker's heartbeat row, replacing the previous one (ADR 0068)."""
        ...

    async def get_worker_heartbeat(self) -> WorkerHeartbeat | None:
        """Return the worker's latest heartbeat row, if it ever wrote one."""
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

    async def get_closure_image(self, closure_id: int) -> ImageFile | None:
        """Return the stored image of the closure, if it has one."""
        ...

    async def save_closure_image(
        self, closure_id: int, text: ImageText, image: ImageFile | None
    ) -> None:
        """Store the closure's image with ``text``; ``image`` ``None`` keeps the stored file.

        Replacing a file deletes the old one: a closure has one image at most.
        """
        ...

    async def remove_closure_image(self, closure_id: int) -> None:
        """Delete the closure's image, if it has one."""
        ...

    async def list_scenarios(
        self, scope: Scope, organisation_id: int, group_id: int | None
    ) -> list[ClosureScenario]:
        """Return the scenarios of a group, or with ``group_id`` ``None`` of the organisation.

        Sorted by name, inside ``scope``: a group scenario when its group is in scope, an
        organisation scenario when its organisation or any of its groups is. The organisation
        is always given, so a group id never reaches another organisation's scenarios.
        """
        ...

    async def get_scenario(self, scenario_id: int) -> ClosureScenario | None:
        """Return the scenario with ``scenario_id`` (the caller checks whose it is)."""
        ...

    async def add_scenario(self, scenario: ClosureScenario) -> ClosureScenario:
        """Store a new scenario and return it with its id."""
        ...

    async def save_scenario(self, scenario: ClosureScenario) -> None:
        """Store the name, texts and contact block of a scenario; its image has its own calls."""
        ...

    async def delete_scenario(self, scenario_id: int) -> None:
        """Delete the scenario and its image."""
        ...

    async def get_scenario_image(self, scenario_id: int) -> ImageFile | None:
        """Return the stored image of the scenario, if it has one."""
        ...

    async def save_scenario_image(
        self, scenario_id: int, text: ImageText, image: ImageFile | None
    ) -> None:
        """Store the scenario's image with ``text``; ``image`` ``None`` keeps the stored file."""
        ...

    async def remove_scenario_image(self, scenario_id: int) -> None:
        """Delete the scenario's image, if it has one."""
        ...

    async def find_confirmed_bookings(
        self, group_id: int, lower: datetime, upper: datetime | None
    ) -> list[Booking]:
        """Return the group's confirmed scheduled bookings with lower <= start < upper.

        ``upper=None`` means no upper bound. For the domain's own use, not for listings.
        """
        ...

    async def list_suspended_bookings(
        self, scope: Scope, group_id: int, status: BookingStatus = BookingStatus.SUSPENDED
    ) -> list[Booking]:
        """Return the group's bookings on hold with ``status``, earliest first.

        ``status`` is ``suspended`` (a closure, ADR 0024) or ``awaiting_rebook`` (a planner's
        request, ADR 0044). The group must lie inside ``scope`` (ADR 0014); otherwise the list
        is empty.
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

    async def delete_api_client(self, client_id: str) -> None:
        """Erase the client's row; its access tokens and group grants go with it."""
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

    async def get_release_listing(self) -> list[ListedRelease]:
        """Return the listing the last successful check stored (ADR 0067), newest first."""
        ...

    async def save_release_listing(self, listing: Sequence[ListedRelease]) -> None:
        """Replace the stored listing."""
        ...

    async def get_running_versions(self, limit: int = 2) -> list[RunningVersion]:
        """Return the versions this installation ran, newest first (ADR 0067)."""
        ...

    async def add_running_version(self, running: RunningVersion) -> RunningVersion:
        """Record a version seen at start-up."""
        ...

    async def list_identity_providers(self, scope: Scope) -> list[OrganisationProvider]:
        """Return the organisation providers of the organisations inside ``scope`` (ADR 0083).

        Providers belong to an organisation, so only the organisation part of the scope (or
        ``everything``) matches them. Ordered by organisation and key.
        """
        ...

    async def get_identity_provider(self, key: str) -> OrganisationProvider | None:
        """Return the provider with ``key`` (unique on the installation)."""
        ...

    async def add_identity_provider(
        self, provider: OrganisationProvider, secret: str | None
    ) -> OrganisationProvider:
        """Store a new provider and return it with its id.

        ``secret`` is sealed by the adapter (ADR 0008) and never read back into the domain.

        Raises:
            LookupError: The organisation does not exist.
        """
        ...

    async def save_identity_provider(
        self, provider: OrganisationProvider, secret: str | None
    ) -> None:
        """Store the changes of an existing provider; ``secret=None`` keeps the stored one."""
        ...

    async def delete_identity_provider(self, key: str) -> bool:
        """Remove the provider, its secret and its keys; ``False`` if there was none."""
        ...

    async def get_signing_keys(self, provider_key: str) -> list[SigningKey]:
        """Return the provider's key pairs (public side), newest first."""
        ...

    async def add_signing_key(self, provider_key: str, key: GeneratedKey, now: datetime) -> None:
        """Store a generated key pair; the private part is sealed by the adapter."""
        ...

    async def retire_signing_keys(self, provider_key: str, *, keep: str, at: datetime) -> None:
        """Mark every current key of the provider except ``keep`` as retired at ``at``."""
        ...

    async def published_signing_keys(self, now: datetime) -> list[SigningKey]:
        """Return the keys of every provider that belong in the JWKS at ``now``."""
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

    async def get_events(self, actions: Sequence[str], limit: int) -> list[AuditEntry]:
        """Return the newest audit events with one of ``actions``, with the actors' names.

        Only for events every admin may see, such as role changes (ADR 0039 item 7).
        """
        ...

    async def get_custom_roles(self) -> list[CustomRole]:
        """Return every custom role, ordered by id (ADR 0039)."""
        ...

    async def get_custom_role(self, role_id: int) -> CustomRole | None:
        """Return the custom role with ``role_id``."""
        ...

    async def add_custom_role(self, role: CustomRole) -> CustomRole:
        """Store a new custom role (its id is ignored) and return it with its id."""
        ...

    async def save_custom_role(self, role: CustomRole) -> None:
        """Store names, claim value, permissions and version of an existing custom role."""
        ...

    async def delete_custom_role(self, role_id: int) -> None:
        """Delete a custom role."""

    async def get_maintenance_state(self) -> MaintenanceState:
        """Return the maintenance switch of the installation (ADR 0038); off if never set."""
        ...

    async def save_maintenance_state(self, state: MaintenanceState) -> None:
        """Replace the maintenance switch."""
        ...

    async def get_certificate_check(self) -> CertificateCheck | None:
        """Return the latest check of the public address's certificate (ADR 0064), if any."""
        ...

    async def save_certificate_check(self, check: CertificateCheck) -> None:
        """Replace the latest certificate check."""
        ...

    async def get_own_certificate(self) -> OwnCertificate | None:
        """Return the uploaded own certificate (ADR 0070); ``None`` without one."""
        ...

    async def save_own_certificate(self, certificate: OwnCertificate) -> None:
        """Store or replace the own certificate's chain and public data (never a key)."""
        ...

    async def delete_own_certificate(self) -> bool:
        """Remove the own certificate; return whether there was one."""
        ...

    async def settings_version(self) -> int:
        """Return the counter that goes up with every settings change (ADR 0042); 0 if never."""
        ...

    async def platform_settings(self) -> dict[str, object]:
        """Return the stored portal values of class C settings by key, as plain JSON values."""
        ...

    async def platform_setting_expiries(self) -> dict[str, datetime]:
        """Return when stored values lapse, for the keys that have an expiry (``LOG_LEVEL``)."""
        ...

    async def save_platform_setting(
        self, key: str, value: object, updated_at: datetime, expires_at: datetime | None
    ) -> None:
        """Store or replace the portal value of ``key`` (a plain JSON value)."""
        ...

    async def delete_platform_setting(self, key: str) -> bool:
        """Remove the portal value of ``key``; return whether there was one."""
        ...

    async def bump_settings_version(self) -> int:
        """Raise ``settings_version`` by one and return the new value (ADR 0042 §6)."""
        ...

    async def platform_secrets(self) -> dict[str, str]:
        """Return the class C secrets set in the portal, opened (empty without a settings key)."""
        ...

    async def secrets_available(self) -> bool:
        """Return whether secrets can be sealed here (``SETTINGS_ENCRYPTION_KEY`` is set)."""
        ...

    async def save_platform_secret(self, key: str, value: str, updated_at: datetime) -> None:
        """Seal and store the portal value of the secret ``key`` (ADR 0008)."""
        ...

    async def delete_platform_secret(self, key: str) -> bool:
        """Remove the sealed portal value of ``key``; return whether there was one."""
        ...

    async def organisation_settings(self) -> dict[int, dict[str, object]]:
        """Return every organisation's stored class D values by organisation id (step 5)."""
        ...

    async def save_organisation_setting(
        self, organisation_id: int, key: str, value: object, updated_at: datetime
    ) -> None:
        """Store or replace one organisation's value of ``key`` (a plain JSON value)."""
        ...

    async def delete_organisation_setting(self, organisation_id: int, key: str) -> bool:
        """Remove one organisation's value of ``key``; return whether there was one."""
        ...

    async def list_log_destinations(self, scope: Scope) -> list[LogDestination]:
        """Return the log destinations inside ``scope``, ordered by name (ADR 0085).

        They belong to the installation, so only a scope that matches everything sees them.
        """
        ...

    async def get_log_destination(self, destination_id: int) -> LogDestination | None:
        """Return the destination with ``destination_id``."""
        ...

    async def add_log_destination(
        self, destination: LogDestination, secret: str | None
    ) -> LogDestination:
        """Store a new destination and return it with its id.

        ``secret`` is sealed by the adapter (ADR 0008) and never read back into the domain.

        Raises:
            DomainError: ``log_destination_name_taken``.
        """
        ...

    async def save_log_destination(self, destination: LogDestination, secret: str | None) -> None:
        """Store the changes of a destination; ``secret=None`` keeps the stored credential.

        Raises:
            DomainError: ``log_destination_not_found`` or ``log_destination_name_taken``.
        """
        ...

    async def delete_log_destination(self, destination_id: int) -> bool:
        """Remove the destination, its credential and its status; ``False`` if there was none."""
        ...

    async def save_log_destination_status(self, status: DestinationStatus, at: datetime) -> None:
        """Store what one process reports about a destination (counters and a reason)."""
        ...

    async def list_log_destination_status(self, scope: Scope) -> list[DestinationStatus]:
        """Return the status every process reported, for a scope that matches everything."""
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
