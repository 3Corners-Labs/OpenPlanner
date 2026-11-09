"""The digest of the usage reports: who manages subscriptions, and the job that sends them.

    DigestAdmin   ``reports.digest.manage`` ─► create / delete a subscription (audited)
    DigestRunner  worker, every poll         ─► due digests ─► recipients ─► one mail each

A subscription names **staff members**, never an address. A person is a recipient only while they
hold ``reports.view`` for the subscription's scope: this is decided when the subscription is
made and again when the mail goes out, so a person who lost the right is skipped and the
subscription says how many. The address is read from the identity provider's directory at send
time (``StaffAddresses``) and is in memory only: it is not in a job, a log or an audit event.
A mail holds the figures the organisation allows (suppressed, like the screens) and a link to the
authenticated report page; it has no attachment.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from app.domain.admin import AdminService, require
from app.domain.audit import Outcome
from app.domain.authorization import (
    Grant,
    Permission,
    Principal,
    RoleCatalogue,
    Scope,
    holds_a_role,
    scope_for,
)
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import (
    Clock,
    DigestUnitOfWork,
    OutboundMessage,
    SenderDirectory,
    StaffAddresses,
)
from app.domain.report_digest import (
    MAX_MONTH_DAY,
    DigestPeriod,
    RunStatus,
    Subscription,
    is_late,
    latest_due,
    window_of,
)
from app.domain.report_digest_model import DigestContent, DigestFigures, DigestMail
from app.domain.staff import StaffFilter, StaffMember, StaffStatus
from app.domain.usage_report import VolumeReport
from app.domain.usage_report_capacity import CapacityReport
from app.domain.usage_report_queue import QueueReport
from app.domain.usage_report_service import (
    UsageReportService,
    build_capacity,
    build_queue,
    build_volume,
)

MAX_RECIPIENTS = 50
"""Staff members one subscription may name; a bound on the stored list, not a tunable."""
DEFAULT_LOCALE = "nl"
"""The language of a digest when the organisation has none (CLAUDE.md: Dutch by default)."""
LIMIT_FIELD = "limit"
"""``DomainError`` field of a refused subscription: the organisation has the most it may."""
RECIPIENT_FIELD = "recipients"
"""``DomainError`` field of a refused subscription: no recipient, or one who may not read."""

Compose = Callable[[DigestContent, str], DigestMail]


def may_read(
    member: StaffMember, catalogue: RoleCatalogue, organisation: str, group: str | None
) -> bool:
    """Return whether ``member`` holds ``reports.view`` for the organisation or the group now."""
    if member.status is not StaffStatus.ACTIVE:
        return False
    grants = frozenset(Grant(m.role, m.organisation, m.group) for m in member.memberships)
    principal = catalogue.holding(Principal(member.subject, member.display_name, grants))
    if not holds_a_role(principal):
        return False
    scope = scope_for(principal, Permission.REPORTS_VIEW)
    if group is not None:
        return scope.contains(organisation, group)
    return scope.everything or organisation in scope.organisations


@dataclass(frozen=True)
class DigestRow:
    """A subscription as the manage page lists it.

    Attributes:
        subscription: The subscription.
        names: The display names of its recipients that still exist.
        blocked: How many recipients no longer hold ``reports.view`` for its scope.
        no_address: How many recipients the identity provider holds no address for now.
        last_status: How the latest due digest ended (a ``RunStatus`` value), or ``None``.
        last_date: Its due date.
    """

    subscription: Subscription
    names: tuple[str, ...]
    blocked: int
    no_address: int
    last_status: str | None
    last_date: date | None


@dataclass(frozen=True)
class Candidate:
    """A colleague who may be named for a scope.

    Attributes:
        member: The staff record.
        has_address: Whether the identity provider holds an address for them now; without one
            they would be skipped when the mail goes out.
    """

    member: StaffMember
    has_address: bool


class DigestAdmin:
    """Subscriptions: list, create and delete, for who holds ``reports.digest.manage``.

    Args:
        uow: Opens transactions with the digest store.
        admin: Decides the permission and audits.
        clock: Current time.
        max_subscriptions: ``reporting_digest_max_subscriptions`` (class C, read each time).
        addresses: Finds a staff member's address, to show who would be skipped.
    """

    def __init__(
        self,
        uow: DigestUnitOfWork,
        admin: AdminService,
        clock: Clock,
        max_subscriptions: Callable[[], int],
        addresses: StaffAddresses,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._admin = admin
        self._clock = clock
        self._max = max_subscriptions
        self._addresses = addresses

    async def _allow(self, principal: Principal, organisation_slug: str) -> Scope:
        """Authorise ``reports.digest.manage`` on the organisation; return the scope it gives."""
        require(
            await self._admin.authorize(
                principal, Permission.REPORTS_DIGEST_MANAGE, organisation_slug=organisation_slug
            )
        )
        return scope_for(principal, Permission.REPORTS_DIGEST_MANAGE)

    async def rows(self, principal: Principal, organisation_slug: str) -> list[DigestRow]:
        """Return the organisation's subscriptions with who gets them and how the last one went.

        Raises:
            DomainError: ``not_permitted`` (a denial is audited).
        """
        scope = await self._allow(principal, organisation_slug)
        out: list[DigestRow] = []
        async with self._uow.read() as tx:
            catalogue = RoleCatalogue(tuple(await tx.get_custom_roles()))
            for sub in await tx.list_subscriptions(scope, organisation_slug):
                names: list[str] = []
                blocked = 0
                no_address = 0
                for staff_id in sub.recipients:
                    member = await tx.get_staff_by_id(staff_id)
                    if member is None:
                        continue
                    names.append(member.display_name)
                    if not may_read(member, catalogue, sub.organisation_slug, sub.group_slug):
                        blocked += 1
                    elif await self._addresses.address_of(member.subject) is None:
                        no_address += 1
                last = await tx.last_digest_run(sub.id) if sub.id is not None else None
                out.append(
                    DigestRow(
                        sub,
                        tuple(names),
                        blocked,
                        no_address,
                        last[1] if last else None,
                        last[0] if last else None,
                    )
                )
        return out

    async def candidates(
        self, principal: Principal, organisation_slug: str, group_slug: str | None
    ) -> list[Candidate]:
        """Return who may be named for the scope: staff of the organisation who may read it.

        Each comes with whether the provider holds an address for them (ADR 0117 point 16).

        Raises:
            DomainError: ``not_permitted`` (a denial is audited).
        """
        scope = await self._allow(principal, organisation_slug)
        async with self._uow.read() as tx:
            catalogue = RoleCatalogue(tuple(await tx.get_custom_roles()))
            found = await tx.list_staff(scope, StaffFilter(organisation=organisation_slug))
        allowed = [m for m in found if may_read(m, catalogue, organisation_slug, group_slug)]
        return [
            Candidate(m, await self._addresses.address_of(m.subject) is not None) for m in allowed
        ]

    async def create(
        self,
        principal: Principal,
        organisation_slug: str,
        *,
        group_slug: str | None,
        period: DigestPeriod,
        weekday: int,
        month_day: int,
        recipients: list[int],
    ) -> int:
        """Store a subscription and audit it; return its id.

        Raises:
            DomainError: ``not_permitted``; ``organisation_not_found`` or ``group_not_found``;
                ``invalid_configuration`` with field ``limit`` (the organisation has the most it
                may), ``recipients`` (none, too many, or one who may not read the scope), or
                ``schedule`` (a weekday or day of the month that does not exist).
        """
        await self._allow(principal, organisation_slug)
        wanted = sorted(set(recipients))
        if not wanted or len(wanted) > MAX_RECIPIENTS:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, RECIPIENT_FIELD)
        if not 0 <= weekday <= 6 or not 1 <= month_day <= MAX_MONTH_DAY:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "schedule")
        async with self._uow.locked() as tx:
            organisation_id = await tx.organisation_id_of(organisation_slug)
            if organisation_id is None:
                raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
            group_id = None
            if group_slug is not None:
                group_id = await tx.group_id_of(organisation_id, group_slug)
                if group_id is None:
                    raise DomainError(ErrorCode.GROUP_NOT_FOUND)
            if await tx.count_subscriptions(organisation_id) >= self._max():
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, LIMIT_FIELD)
            catalogue = RoleCatalogue(tuple(await tx.get_custom_roles()))
            for staff_id in wanted:
                member = await tx.get_staff_by_id(staff_id)
                if member is None or not may_read(member, catalogue, organisation_slug, group_slug):
                    raise DomainError(ErrorCode.INVALID_CONFIGURATION, RECIPIENT_FIELD)
            created = Subscription(
                None,
                organisation_id,
                organisation_slug,
                group_id,
                group_slug,
                period,
                weekday,
                month_day,
                tuple(wanted),
                principal.subject,
                self._clock.now(),
            )
            new_id = await tx.add_subscription(created)
        await self._audit(principal, organisation_slug, group_slug, f"create:{period.value}")
        return new_id

    async def delete(
        self, principal: Principal, organisation_slug: str, subscription_id: int
    ) -> None:
        """Delete a subscription of the organisation and audit it.

        Raises:
            DomainError: ``not_permitted``; ``group_not_found`` for a subscription that is not
                this organisation's (it is not told apart from one that does not exist).
        """
        await self._allow(principal, organisation_slug)
        async with self._uow.locked() as tx:
            found = await tx.get_subscription(subscription_id)
            if found is None or found.organisation_slug != organisation_slug:
                raise DomainError(ErrorCode.GROUP_NOT_FOUND)
            await tx.delete_subscription(subscription_id)
        await self._audit(principal, organisation_slug, found.group_slug, "delete")

    async def _audit(
        self, principal: Principal, organisation: str, group: str | None, detail: str
    ) -> None:
        """Audit a change of a subscription: the permission is the action, no recipient in it."""
        await self._admin.record(
            principal.subject,
            Permission.REPORTS_DIGEST_MANAGE.value,
            Outcome.SUCCESS,
            "",
            organisation,
            group,
            detail,
        )


@dataclass(frozen=True)
class DigestRun:
    """What one pass of the runner did, in counts and error types; the worker logs it.

    Attributes:
        sent: Mails handed to the sender.
        skipped: Recipients left out (lost the right, or no address).
        digests: Due digests that were run.
        late: Digests skipped for being later than allowed.
        no_channel: Digests held back because the organisation has no mail channel.
        failed: Mails the sender refused.
        errors: The type names of errors that stopped a subscription.
    """

    sent: int = 0
    skipped: int = 0
    digests: int = 0
    late: int = 0
    no_channel: int = 0
    failed: int = 0
    errors: tuple[str, ...] = ()

    def __add__(self, other: "DigestRun") -> "DigestRun":
        """Return the sum of two passes."""
        return DigestRun(
            self.sent + other.sent,
            self.skipped + other.skipped,
            self.digests + other.digests,
            self.late + other.late,
            self.no_channel + other.no_channel,
            self.failed + other.failed,
            self.errors + other.errors,
        )


class DigestRunner:
    """Sends the digests that are due; called on every poll of the worker.

    Args:
        uow: Opens transactions with the digest store.
        clock: Current time.
        reports: Reads the figures of a scope the subscription already fixed.
        senders: Finds the organisation's email sender.
        addresses: Finds a staff member's address.
        compose: Writes the mail in a language (``app/web/digest_text.py``).
        base_url: The portal's public address, for the link.
        send_hour: ``reporting_digest_send_hour`` of an organisation (class D).
        include_numbers: ``reporting_digest_include_numbers`` of an organisation (class D).
        late_hours: ``reporting_digest_late_hours`` (class C).
        fallback_zone: The zone of an organisation without a group (``ADMIN_TIMEZONE``).
    """

    def __init__(
        self,
        uow: DigestUnitOfWork,
        clock: Clock,
        reports: UsageReportService,
        senders: SenderDirectory,
        addresses: StaffAddresses,
        compose: Compose,
        base_url: str,
        send_hour: Callable[[int], int],
        include_numbers: Callable[[int], bool],
        late_hours: Callable[[], int],
        fallback_zone: str,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._reports = reports
        self._senders = senders
        self._addresses = addresses
        self._compose = compose
        self._base = base_url.rstrip("/")
        self._send_hour = send_hour
        self._include = include_numbers
        self._late = late_hours
        self._fallback = fallback_zone

    async def run(self) -> DigestRun:
        """Send every digest that is due and not yet run; return the counts of this pass.

        One failing subscription never stops the others.
        """
        async with self._uow.read() as tx:
            subscriptions = await tx.list_all_subscriptions()
        total = DigestRun()
        for sub in subscriptions:
            try:
                done = await self._one(sub)
            except Exception as exc:  # one subscription must not stop the others
                total += DigestRun(errors=(type(exc).__name__,))
                continue
            total += done
        return total

    async def _one(self, sub: Subscription) -> DigestRun:
        """Send the due digest of one subscription, if there is one."""
        if sub.id is None:
            raise ValueError("a stored subscription has an id")
        now = self._clock.now()
        hour = self._send_hour(sub.organisation_id)
        async with self._uow.read() as tx:
            zone_name = await tx.digest_zone(sub.organisation_id, sub.group_id)
            zone = ZoneInfo(zone_name or self._fallback)
            day = latest_due(sub, now, zone, hour)
            if day is None or await tx.has_digest_run(sub.id, day):
                return DigestRun()
        if is_late(day, now, zone, hour, self._late()):
            async with self._uow.locked() as tx:
                await tx.claim_digest_run(sub.id, day, RunStatus.SKIPPED_LATE, 0, 0, now)
            return DigestRun(digests=1, late=1)
        sender = await self._senders.sender(sub.organisation_id, ChannelKind.EMAIL)
        if sender is None:
            return DigestRun(no_channel=1)  # not claimed: the next poll tries again until late
        mails, skipped, locale = await self._recipients(sub)
        if not mails:
            async with self._uow.locked() as tx:
                await tx.claim_digest_run(sub.id, day, RunStatus.NO_RECIPIENT, 0, skipped, now)
            return DigestRun(skipped=skipped, digests=1)
        first, last = window_of(sub, day)
        content = await self._content(sub, first, last)
        mail = self._compose(content, locale)
        async with self._uow.locked() as tx:
            if not await tx.claim_digest_run(sub.id, day, RunStatus.SENT, len(mails), skipped, now):
                return DigestRun()  # another worker was first
        failed = 0
        for address in mails:
            try:
                await sender.send(
                    OutboundMessage(ChannelKind.EMAIL, address, mail.subject, mail.text, mail.html)
                )
            except Exception:  # a failing mailbox must not stop the others
                failed += 1
        return DigestRun(sent=len(mails) - failed, skipped=skipped, digests=1, failed=failed)

    async def _recipients(self, sub: Subscription) -> tuple[list[str], int, str]:
        """Return the addresses to mail now, how many were skipped, and the language."""
        addresses: list[str] = []
        skipped = 0
        async with self._uow.read() as tx:
            catalogue = RoleCatalogue(tuple(await tx.get_custom_roles()))
            locale = await tx.digest_locale(sub.organisation_id) or DEFAULT_LOCALE
            members = [await tx.get_staff_by_id(i) for i in sub.recipients]
        for member in members:
            if member is None or not may_read(
                member, catalogue, sub.organisation_slug, sub.group_slug
            ):
                skipped += 1
                continue
            address = await self._addresses.address_of(member.subject)
            if address is None:
                skipped += 1
                continue
            addresses.append(address)
        return addresses, skipped, locale

    async def _content(self, sub: Subscription, first: date, last: date) -> DigestContent:
        """Return what the mail says: the covered days, the link, the figures if allowed."""
        scope = (
            Scope(groups=frozenset({(sub.organisation_slug, sub.group_slug)}))
            if sub.group_slug
            else Scope(organisations=frozenset({sub.organisation_slug}))
        )
        path = (
            f"/admin/groups/{sub.group_slug}/usage"
            if sub.group_slug
            else f"/admin/organisations/{sub.organisation_slug}/usage"
        )
        link = f"{self._base}{path}?from={first.isoformat()}&to={last.isoformat()}"
        figures = None
        if self._include(sub.organisation_id):
            loaded = await self._reports.read(
                scope, sub.organisation_slug, sub.group_slug, first, last
            )
            figures = figures_of(build_volume(loaded), build_queue(loaded), build_capacity(loaded))
        return DigestContent(
            sub.organisation_slug,
            sub.organisation_id,
            sub.group_slug,
            sub.group_id,
            first,
            last,
            link,
            figures,
        )


def figures_of(volume: VolumeReport, queue: QueueReport, capacity: CapacityReport) -> DigestFigures:
    """Return the digest's four figures from the reports; a hidden one stays ``None``."""
    shown = {row.code: row.count for row in volume.outcomes.rows}
    done, missed = shown.get("completed"), shown.get("no_show")
    no_show = (
        round(100 * missed / (done + missed))
        if done is not None and missed is not None and done + missed
        else None
    )
    wait = queue.task_wait.mean_seconds
    return DigestFigures(
        volume.created_total,
        no_show,
        None if wait is None else round(wait / 60),
        capacity.utilisation_percent,
    )
