"""SQL implementation of the domain's ``UnitOfWork`` and ``Transaction`` ports.

``locked()`` starts the transaction with ``BEGIN IMMEDIATE``: SQLite takes the write lock up
front, so the capacity check and the insert of a booking can never interleave with another
writer. ``read()`` uses a deferred ``BEGIN``; in WAL mode readers never wait for writers.

All mapping between rows and domain objects lives in this module.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

from sqlalchemy import ColumnElement, case, delete, false, func, insert, or_, select, true, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.sql.elements import SQLCoreOperations

from app.adapters.db.engine import BEGIN_MODE_OPTION
from app.adapters.db.models import (
    AdminProfileRow,
    AuditEventRow,
    BookingRow,
    GroupRow,
    JobRow,
    OrganisationRow,
    ReminderRuleRow,
    ScheduleOverrideRow,
    ScheduleRuleRow,
    SlotRow,
)
from app.domain.audit import AdminProfile, AuditEntry, AuditEvent, Outcome
from app.domain.authorization import Scope
from app.domain.booking import Booking, BookingStatus, Contact, Slot, SlotUsage
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import PlannedJob
from app.domain.scheduling import (
    Group,
    GroupSummary,
    Organisation,
    PhoneMode,
    ScheduleOverride,
    ScheduleRule,
    TimeWindow,
)

_PENDING = "pending"


def _organisation(row: OrganisationRow) -> Organisation:
    """Map an organisation row to the domain."""
    return Organisation(row.id, row.name, row.slug, row.default_locale)


def _group(row: GroupRow) -> Group:
    """Map a group row to the domain."""
    return Group(
        id=row.id,
        organisation_id=row.organisation_id,
        name=row.name,
        slug=row.slug,
        timezone=row.timezone,
        slot_minutes=row.slot_minutes,
        min_notice_minutes=row.min_notice_minutes,
        horizon_days=row.horizon_days,
        phone_mode=PhoneMode(row.phone_mode),
        video_host_email=row.video_host_email,
        wxcc_business_address=row.wxcc_business_address,
        wxcc_channel=row.wxcc_channel,
        wxcc_routing_value=row.wxcc_routing_value,
        direct_call_enabled=row.direct_call_enabled,
        not_picked_up_minutes=row.not_picked_up_minutes,
        dispatch_lead_minutes=row.dispatch_lead_minutes,
    )


def scope_filter(
    scope: Scope,
    organisation_slug: SQLCoreOperations[Any],
    group_slug: SQLCoreOperations[Any] | None,
) -> ColumnElement[bool]:
    """Translate a ``Scope`` into a SQL condition; the one place this happens (ADR 0014).

    An empty scope matches nothing; ``everything`` matches all rows.
    """
    if scope.everything:
        return true()
    conditions: list[ColumnElement[bool]] = []
    if scope.organisations:
        conditions.append(organisation_slug.in_(sorted(scope.organisations)))
    if scope.groups and group_slug is not None:
        conditions.append(group_slug.in_(sorted(scope.groups)))
    return or_(*conditions) if conditions else false()


def _override(row: ScheduleOverrideRow) -> ScheduleOverride:
    """Map an override row; NULL window columns mean a closed day."""
    if row.opens is None or row.closes is None or row.capacity is None:
        return ScheduleOverride(day=row.day, window=None, id=row.id)
    return ScheduleOverride(
        day=row.day, window=TimeWindow(row.opens, row.closes, row.capacity), id=row.id
    )


def _rule(row: ScheduleRuleRow) -> ScheduleRule:
    """Map a weekly rule row."""
    return ScheduleRule(
        weekday=row.weekday,
        window=TimeWindow(row.opens, row.closes, row.capacity),
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        id=row.id,
    )


def _group_columns(group: Group) -> dict[str, Any]:
    """Return the stored columns of a group's settings."""
    return {
        "name": group.name,
        "timezone": group.timezone,
        "slot_minutes": group.slot_minutes,
        "min_notice_minutes": group.min_notice_minutes,
        "horizon_days": group.horizon_days,
        "phone_mode": group.phone_mode.value,
        "video_host_email": group.video_host_email,
        "wxcc_business_address": group.wxcc_business_address,
        "wxcc_channel": group.wxcc_channel,
        "wxcc_routing_value": group.wxcc_routing_value,
        "direct_call_enabled": group.direct_call_enabled,
        "not_picked_up_minutes": group.not_picked_up_minutes,
        "dispatch_lead_minutes": group.dispatch_lead_minutes,
    }


def _booking(row: BookingRow, slot: SlotRow) -> Booking:
    """Map a booking row and its slot; a purged booking has no contact."""
    contact = (
        Contact(name=row.name, email=row.email, phone=row.phone)
        if row.name is not None and row.email is not None
        else None
    )
    return Booking(
        id=row.id,
        group_id=row.group_id,
        slot_id=row.slot_id,
        start=slot.start,
        end=slot.end,
        status=BookingStatus(row.status),
        contact=contact,
        locale=row.locale,
        token_hash=row.management_token_hash,
        created_at=row.created_at,
        token_salt=row.management_token_salt,
        consents=frozenset(ChannelKind(c) for c in row.consents.split(",") if c),
        cancelled_at=row.cancelled_at,
        rescheduled_from_id=row.rescheduled_from_id,
    )


class SqlTransaction:
    """``Transaction`` on an ``AsyncSession`` whose transaction is already open."""

    def __init__(self, session: AsyncSession) -> None:
        """Wrap ``session``."""
        self._session = session

    async def list_groups(self, scope: Scope) -> list[GroupSummary]:
        """Return the groups inside ``scope``, ordered by organisation and name."""
        rows = await self._session.execute(
            select(GroupRow, OrganisationRow)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(scope_filter(scope, OrganisationRow.slug, GroupRow.slug))
            .order_by(OrganisationRow.name, GroupRow.name)
        )
        return [GroupSummary(_group(group), org.slug, org.name) for group, org in rows]

    async def get_group_summary(self, slug: str) -> GroupSummary | None:
        """Return one group with its organisation, for an authorisation decision."""
        found = (
            await self._session.execute(
                select(GroupRow, OrganisationRow)
                .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
                .where(GroupRow.slug == slug)
            )
        ).one_or_none()
        if found is None:
            return None
        group, organisation = found
        return GroupSummary(_group(group), organisation.slug, organisation.name)

    async def add_audit_event(self, event: AuditEvent) -> None:
        """Store an audit event."""
        self._session.add(
            AuditEventRow(
                at=event.at,
                actor=event.actor[:255],
                action=event.action[:64],
                outcome=event.outcome.value,
                reason=event.reason[:64],
                organisation=event.organisation,
                group=event.group,
                detail=event.detail[:255],
            )
        )
        await self._session.flush()

    async def list_audit_events(self, scope: Scope, limit: int = 100) -> list[AuditEntry]:
        """Return the newest audit events about organisations and groups inside ``scope``."""
        rows = await self._session.execute(
            select(AuditEventRow, AdminProfileRow.display_name)
            .outerjoin(AdminProfileRow, AdminProfileRow.subject == AuditEventRow.actor)
            .where(scope_filter(scope, AuditEventRow.organisation, AuditEventRow.group))
            .order_by(AuditEventRow.at.desc(), AuditEventRow.id.desc())
            .limit(limit)
        )
        return [
            AuditEntry(
                AuditEvent(
                    at=row.at,
                    actor=row.actor,
                    action=row.action,
                    outcome=Outcome(row.outcome),
                    reason=row.reason,
                    organisation=row.organisation,
                    group=row.group,
                    detail=row.detail,
                ),
                name,
            )
            for row, name in rows
        ]

    async def save_admin_profile(self, profile: AdminProfile) -> None:
        """Create or replace the profile of ``profile.subject``."""
        await self._session.merge(
            AdminProfileRow(
                subject=profile.subject[:255],
                display_name=profile.display_name[:255],
                last_login_at=profile.last_login_at,
            )
        )
        await self._session.flush()

    async def get_group(self, slug: str) -> Group | None:
        """Return the group with ``slug``."""
        row = await self._session.scalar(select(GroupRow).where(GroupRow.slug == slug))
        return _group(row) if row else None

    async def get_group_by_id(self, group_id: int) -> Group | None:
        """Return the group with ``group_id``."""
        row = await self._session.get(GroupRow, group_id)
        return _group(row) if row else None

    async def get_schedule(
        self, group_id: int, first_day: date, last_day: date
    ) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
        """Return all rules of the group and its overrides between the two dates."""
        rules = await self._session.scalars(
            select(ScheduleRuleRow).where(ScheduleRuleRow.group_id == group_id)
        )
        overrides = await self._session.scalars(
            select(ScheduleOverrideRow).where(
                ScheduleOverrideRow.group_id == group_id,
                ScheduleOverrideRow.day.between(first_day, last_day),
            )
        )
        return [_rule(r) for r in rules], [_override(o) for o in overrides]

    async def list_organisations(self, scope: Scope) -> list[Organisation]:
        """Return the organisations inside ``scope`` (organisation scope only), by slug."""
        rows = await self._session.scalars(
            select(OrganisationRow)
            .where(scope_filter(scope, OrganisationRow.slug, None))
            .order_by(OrganisationRow.slug)
        )
        return [_organisation(row) for row in rows]

    async def get_organisation(self, slug: str) -> Organisation | None:
        """Return the organisation with ``slug``."""
        row = await self._session.scalar(
            select(OrganisationRow).where(OrganisationRow.slug == slug)
        )
        return _organisation(row) if row else None

    async def add_group(self, group: Group) -> Group:
        """Insert a group and return it with its ID."""
        row = GroupRow(
            organisation_id=group.organisation_id, slug=group.slug, **_group_columns(group)
        )
        self._session.add(row)
        await self._session.flush()
        return _group(row)

    async def save_group(self, group: Group) -> None:
        """Store the settings of an existing group; slug and organisation never change."""
        await self._session.execute(
            update(GroupRow).where(GroupRow.id == group.id).values(**_group_columns(group))
        )

    async def count_future_bookings(self, group_id: int, now: datetime) -> int:
        """Return how many confirmed bookings of the group start after ``now``."""
        count = await self._session.scalar(
            select(func.count(BookingRow.id))
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .where(
                BookingRow.group_id == group_id,
                BookingRow.status == BookingStatus.CONFIRMED.value,
                SlotRow.start > now,
            )
        )
        return int(count or 0)

    async def get_schedule_items(
        self, group_id: int, from_day: date | None = None
    ) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
        """Return the group's rules and overrides (from ``from_day`` on), each with its id."""
        rules = await self._session.scalars(
            select(ScheduleRuleRow)
            .where(ScheduleRuleRow.group_id == group_id)
            .order_by(ScheduleRuleRow.weekday, ScheduleRuleRow.opens)
        )
        query = select(ScheduleOverrideRow).where(ScheduleOverrideRow.group_id == group_id)
        if from_day is not None:
            query = query.where(ScheduleOverrideRow.day >= from_day)
        overrides = await self._session.scalars(
            query.order_by(ScheduleOverrideRow.day, ScheduleOverrideRow.opens)
        )
        return [_rule(r) for r in rules], [_override(o) for o in overrides]

    async def add_schedule_rule(self, group_id: int, rule: ScheduleRule) -> int:
        """Insert a weekly rule and return its ID."""
        row = ScheduleRuleRow(
            group_id=group_id,
            weekday=rule.weekday,
            opens=rule.window.opens,
            closes=rule.window.closes,
            capacity=rule.window.capacity,
            valid_from=rule.valid_from,
            valid_to=rule.valid_to,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def delete_schedule_rule(self, group_id: int, rule_id: int) -> bool:
        """Delete the group's rule; the group condition keeps other groups' rules out of reach."""
        result = await self._session.execute(
            delete(ScheduleRuleRow).where(
                ScheduleRuleRow.id == rule_id, ScheduleRuleRow.group_id == group_id
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def add_schedule_override(self, group_id: int, override: ScheduleOverride) -> int:
        """Insert an override (a closed day when it has no window) and return its ID."""
        window = override.window
        row = ScheduleOverrideRow(
            group_id=group_id,
            day=override.day,
            opens=window.opens if window else None,
            closes=window.closes if window else None,
            capacity=window.capacity if window else None,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def delete_schedule_override(self, group_id: int, override_id: int) -> bool:
        """Delete the group's override; the group condition keeps others out of reach."""
        result = await self._session.execute(
            delete(ScheduleOverrideRow).where(
                ScheduleOverrideRow.id == override_id, ScheduleOverrideRow.group_id == group_id
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def get_reminder_rules(self, group_id: int) -> list[ReminderRule]:
        """Return the group's reminder rules."""
        rows = await self._session.scalars(
            select(ReminderRuleRow).where(ReminderRuleRow.group_id == group_id)
        )
        return [ReminderRule(ChannelKind(r.channel), r.minutes_before, r.enabled) for r in rows]

    async def get_slot_usage(
        self, group_id: int, lower: datetime, upper: datetime
    ) -> list[SlotUsage]:
        """Return capacity and active bookings of existing slots, in one query."""
        active = func.count(case((BookingRow.status == BookingStatus.CONFIRMED.value, 1)))
        rows = await self._session.execute(
            select(SlotRow.start, SlotRow.capacity_snapshot, active)
            .outerjoin(BookingRow, BookingRow.slot_id == SlotRow.id)
            .where(SlotRow.group_id == group_id, SlotRow.start >= lower, SlotRow.start < upper)
            .group_by(SlotRow.id)
        )
        return [SlotUsage(start, capacity, int(count)) for start, capacity, count in rows]

    async def get_slot(self, group_id: int, start: datetime) -> Slot | None:
        """Return the existing slot of the group at ``start``."""
        row = await self._session.scalar(
            select(SlotRow).where(SlotRow.group_id == group_id, SlotRow.start == start)
        )
        if row is None:
            return None
        return Slot(row.id, row.group_id, row.start, row.end, row.capacity_snapshot)

    async def add_slot(self, slot: Slot) -> Slot:
        """Insert a slot and return it with its ID."""
        row = SlotRow(
            group_id=slot.group_id,
            start=slot.start,
            end=slot.end,
            capacity_snapshot=slot.capacity_snapshot,
        )
        self._session.add(row)
        await self._session.flush()
        return Slot(row.id, slot.group_id, slot.start, slot.end, slot.capacity_snapshot)

    async def count_active_bookings(self, slot_id: int) -> int:
        """Return the number of confirmed bookings in the slot."""
        count = await self._session.scalar(
            select(func.count()).where(
                BookingRow.slot_id == slot_id,
                BookingRow.status == BookingStatus.CONFIRMED.value,
            )
        )
        return int(count or 0)

    async def add_booking(self, booking: Booking) -> Booking:
        """Insert a booking and return it with its ID."""
        contact = booking.contact
        row = BookingRow(
            slot_id=booking.slot_id,
            group_id=booking.group_id,
            status=booking.status.value,
            name=contact.name if contact else None,
            email=contact.email if contact else None,
            phone=contact.phone if contact else None,
            locale=booking.locale,
            consents=",".join(sorted(booking.consents)),
            management_token_hash=booking.token_hash,
            management_token_salt=booking.token_salt,
            created_at=booking.created_at,
            cancelled_at=booking.cancelled_at,
            rescheduled_from_id=booking.rescheduled_from_id,
        )
        self._session.add(row)
        await self._session.flush()
        return _booking(row, await self._slot_row(row.slot_id))

    async def get_booking(self, booking_id: int) -> Booking | None:
        """Return the booking with ``booking_id``."""
        result = await self._session.execute(
            select(BookingRow, SlotRow)
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .where(BookingRow.id == booking_id)
        )
        found = result.one_or_none()
        return _booking(*found) if found else None

    async def purge_personal_data(self, booking_id: int, now: datetime) -> None:
        """NULL every PII column (CLAUDE.md privacy rules) and record ``pii_purged_at``."""
        await self._session.execute(
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(
                name=None,
                email=None,
                phone=None,
                guest_join_url=None,
                host_join_url=None,
                pii_purged_at=now,
            )
        )

    async def get_booking_by_token_hash(self, token_hash: str) -> Booking | None:
        """Return the booking whose management token hashes to ``token_hash``."""
        result = await self._session.execute(
            select(BookingRow, SlotRow)
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .where(BookingRow.management_token_hash == token_hash)
        )
        found = result.one_or_none()
        return _booking(*found) if found else None

    async def save_booking(self, booking: Booking) -> None:
        """Persist the mutable fields of a booking (status and cancellation time)."""
        await self._session.execute(
            update(BookingRow)
            .where(BookingRow.id == booking.id)
            .values(status=booking.status.value, cancelled_at=booking.cancelled_at)
        )

    async def replace_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        """Delete the booking's pending jobs and insert ``jobs``."""
        await self._session.execute(
            delete(JobRow).where(JobRow.booking_id == booking_id, JobRow.status == _PENDING)
        )
        if jobs:
            await self._session.execute(
                insert(JobRow),
                [
                    {
                        "booking_id": booking_id,
                        "type": job.type.value,
                        "run_at": job.run_at,
                        "pinned_start": job.pinned_start,
                        "channel": job.channel.value if job.channel else None,
                    }
                    for job in jobs
                ],
            )

    async def _slot_row(self, slot_id: int) -> SlotRow:
        """Return the slot row of a booking (it always exists: foreign key)."""
        row = await self._session.get(SlotRow, slot_id)
        if row is None:  # pragma: no cover - guarded by the foreign key
            raise LookupError("slot missing")
        return row


def _is_lock_timeout(exc: OperationalError) -> bool:
    """Return whether SQLite gave up waiting for the write lock."""
    message = str(exc.orig).lower()
    return "database is locked" in message or "database is busy" in message


class SqlUnitOfWork:
    """``UnitOfWork`` over an ``AsyncEngine``.

    Args:
        engine: Engine created by ``app.adapters.db.engine.create_engine``.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        """Store the engine."""
        self._engine = engine

    @asynccontextmanager
    async def read(self) -> AsyncIterator[SqlTransaction]:
        """Open a read transaction (deferred ``BEGIN``)."""
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            async with session.begin():
                yield SqlTransaction(session)

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[SqlTransaction]:
        """Open a write transaction with ``BEGIN IMMEDIATE``; commit on success.

        Raises:
            DomainError: ``database_busy`` if the write lock is not obtained in time.
        """
        async with locked_session(self._engine) as session:
            yield SqlTransaction(session)


@asynccontextmanager
async def locked_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield a session whose transaction starts with ``BEGIN IMMEDIATE``; commit on success.

    Shared by the unit of work and the job queue, so every writer takes the lock the same way.

    Raises:
        DomainError: ``database_busy`` if the write lock is not obtained in time.
    """
    async with engine.connect() as raw:
        connection = await raw.execution_options(**{BEGIN_MODE_OPTION: "IMMEDIATE"})
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            try:
                async with session.begin():
                    yield session
            except OperationalError as exc:
                if _is_lock_timeout(exc):
                    raise DomainError(ErrorCode.DATABASE_BUSY) from exc
                raise
