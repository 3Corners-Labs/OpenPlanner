"""SQL implementation of the domain's ``UnitOfWork`` and ``Transaction`` ports.

``locked()`` starts the transaction with ``BEGIN IMMEDIATE``: SQLite takes the write lock up
front, so the capacity check and the insert of a booking can never interleave with another
writer. ``read()`` uses a deferred ``BEGIN``; in WAL mode readers never wait for writers.

All mapping between rows and domain objects lives in this module.
"""

import json
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from sqlalchemy import ColumnElement, case, delete, false, func, insert, or_, select, true, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.sql.elements import SQLCoreOperations

from app.adapters.crypto import SecretBox
from app.adapters.db.api_clients import ApiClientQueries
from app.adapters.db.engine import BEGIN_MODE_OPTION
from app.adapters.db.models import (
    AdminProfileRow,
    AuditEventRow,
    BookingEventRow,
    BookingRow,
    CallStatusRow,
    ChannelSettingsRow,
    ClosureNoticeRow,
    ClosureRow,
    GroupHolidayCalendarRow,
    GroupHolidayOpenDayRow,
    GroupRow,
    GroupTextRow,
    HolidayCalendarRow,
    HolidayRow,
    JobRow,
    OrganisationHolidayCalendarRow,
    OrganisationLogoRow,
    OrganisationRow,
    ReminderRuleRow,
    ScheduleOverrideRow,
    ScheduleRuleRow,
    SlotRow,
    WebhookEventRow,
)
from app.domain.audit import AdminProfile, AuditEntry, AuditEvent, Outcome
from app.domain.authorization import Scope
from app.domain.booking import (
    HOLDS_PLACE,
    Booking,
    BookingKind,
    BookingStatus,
    Contact,
    MeetingRef,
    Slot,
    SlotUsage,
)
from app.domain.branding import Branding, Logo, LogoRef
from app.domain.calls import (
    BookingEvent,
    BookingOverview,
    CallState,
    CallStatus,
    EventKind,
    TaskState,
    new_status,
)
from app.domain.channel_settings import (
    ChannelConfig,
    ChannelSettings,
    SmsSettings,
    SmtpSecurity,
    SmtpSettings,
)
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.closure import Closure
from app.domain.errors import DomainError, ErrorCode
from app.domain.holidays import GroupHolidays, Holiday, HolidayCalendar, HolidaySource
from app.domain.job_plan import JobType, PlannedJob
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
_HOLDS_PLACE = sorted(status.value for status in HOLDS_PLACE)


def _organisation(row: OrganisationRow) -> Organisation:
    """Map an organisation row, with its branding, to the domain."""
    logo = (
        LogoRef(row.logo_media_type, row.logo_digest)
        if row.logo_media_type and row.logo_digest
        else None
    )
    branding = Branding(row.display_name, row.accent_color, logo)
    return Organisation(row.id, row.name, row.slug, row.default_locale, branding)


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


def _calendar(row: HolidayCalendarRow) -> HolidayCalendar:
    """Map a holiday calendar row."""
    return HolidayCalendar(row.id, row.slug, row.name)


def _holiday(row: HolidayRow) -> Holiday:
    """Map a holiday row."""
    return Holiday(row.calendar_id, row.day, row.name, id=row.id)


def _rule(row: ScheduleRuleRow) -> ScheduleRule:
    """Map a weekly rule row."""
    return ScheduleRule(
        weekday=row.weekday,
        window=TimeWindow(row.opens, row.closes, row.capacity),
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        id=row.id,
    )


def _rule_columns(rule: ScheduleRule) -> dict[str, Any]:
    """Return the stored columns of a weekly rule (without its id and group)."""
    return {
        "weekday": rule.weekday,
        "opens": rule.window.opens,
        "closes": rule.window.closes,
        "capacity": rule.window.capacity,
        "valid_from": rule.valid_from,
        "valid_to": rule.valid_to,
    }


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
        meeting=MeetingRef(row.video_room_id, row.guest_join_url) if row.video_room_id else None,
        wxcc_task_id=row.wxcc_task_id,
        kind=BookingKind(slot.kind),
        closure_id=row.closure_id,
    )


def _closure(row: ClosureRow, notices: Sequence[ClosureNoticeRow]) -> Closure:
    """Map a closure row with its notices."""
    return Closure(
        id=row.id,
        group_id=row.group_id,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        notices={n.locale: n.text for n in notices},
        created_at=row.created_at,
    )


def _call_status(row: CallStatusRow) -> CallStatus:
    """Map a call status row."""
    return CallStatus(
        booking_id=row.booking_id,
        call=CallState(row.call),
        task=TaskState(row.task),
        participants=row.participants,
        host_joined=row.host_joined,
        host_joined_at=row.host_joined_at,
        started_at=row.started_at,
        ended_at=row.ended_at,
        task_created_at=row.task_created_at,
        connected_at=row.connected_at,
        updated_at=row.updated_at,
    )


def _channel_config(channel: ChannelKind, settings: str) -> ChannelConfig:
    """Map the JSON of a channel settings row back to the domain (validating it again)."""
    values = json.loads(settings)
    if channel is ChannelKind.EMAIL:
        return SmtpSettings(**{**values, "security": SmtpSecurity(values["security"])})
    return SmsSettings(**values)


def _secret_name(organisation_id: int, channel: ChannelKind) -> str:
    """Return the record name a channel secret is sealed under (associated data, ADR 0008)."""
    return f"channel:{organisation_id}:{channel.value}"


class SqlTransaction(ApiClientQueries):
    """``Transaction`` on an ``AsyncSession`` whose transaction is already open.

    The machine API's queries come from ``ApiClientQueries`` (ADR 0023).

    Args:
        session: The open session.
        box: Seals and opens channel secrets; ``None`` when no settings key is configured.
    """

    def __init__(self, session: AsyncSession, box: SecretBox | None = None) -> None:
        """Wrap ``session``."""
        self._session = session
        self._box = box

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

    async def get_organisation_by_id(self, organisation_id: int) -> Organisation | None:
        """Return the organisation with ``organisation_id``."""
        row = await self._session.get(OrganisationRow, organisation_id)
        return _organisation(row) if row else None

    async def add_organisation(self, organisation: Organisation) -> Organisation:
        """Insert an organisation with default branding and return it with its ID."""
        row = OrganisationRow(
            name=organisation.name,
            slug=organisation.slug,
            default_locale=organisation.default_locale,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _organisation(row)

    async def save_organisation(self, organisation: Organisation) -> None:
        """Store name and default locale; slug and branding columns are left alone."""
        await self._session.execute(
            update(OrganisationRow)
            .where(OrganisationRow.id == organisation.id)
            .values(name=organisation.name, default_locale=organisation.default_locale)
        )

    async def save_branding(self, organisation_id: int, branding: Branding) -> None:
        """Store display name and accent colour; the logo columns are left alone."""
        await self._session.execute(
            update(OrganisationRow)
            .where(OrganisationRow.id == organisation_id)
            .values(display_name=branding.display_name, accent_color=branding.accent_color)
        )

    async def get_logo(self, organisation_slug: str) -> Logo | None:
        """Return the organisation's logo, if it has one."""
        found = (
            await self._session.execute(
                select(OrganisationRow.logo_media_type, OrganisationLogoRow.data)
                .join(
                    OrganisationLogoRow, OrganisationLogoRow.organisation_id == OrganisationRow.id
                )
                .where(OrganisationRow.slug == organisation_slug)
            )
        ).first()
        if found is None or found[0] is None:
            return None
        return Logo(found[0], found[1])

    async def save_logo(self, organisation_id: int, logo: Logo | None) -> None:
        """Replace or remove the logo and keep its type and digest on the organisation."""
        await self._session.execute(
            delete(OrganisationLogoRow).where(
                OrganisationLogoRow.organisation_id == organisation_id
            )
        )
        if logo is not None:
            self._session.add(OrganisationLogoRow(organisation_id=organisation_id, data=logo.data))
        await self._session.execute(
            update(OrganisationRow)
            .where(OrganisationRow.id == organisation_id)
            .values(
                logo_media_type=logo.media_type if logo else None,
                logo_digest=logo.digest if logo else None,
            )
        )

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
        """Return how many bookings of the group that hold a place start after ``now``."""
        count = await self._session.scalar(
            select(func.count(BookingRow.id))
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .where(
                BookingRow.group_id == group_id,
                BookingRow.status.in_(_HOLDS_PLACE),
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
        row = ScheduleRuleRow(group_id=group_id, **_rule_columns(rule))
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def update_schedule_rule(self, group_id: int, rule_id: int, rule: ScheduleRule) -> bool:
        """Replace the group's rule; the group condition keeps other groups' rules out of reach."""
        result = await self._session.execute(
            update(ScheduleRuleRow)
            .where(ScheduleRuleRow.id == rule_id, ScheduleRuleRow.group_id == group_id)
            .values(**_rule_columns(rule))
        )
        return bool(getattr(result, "rowcount", 0))

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

    async def get_holiday_calendars(self) -> list[HolidayCalendar]:
        """Return every holiday calendar, by name (global reference data, ADR 0025)."""
        rows = await self._session.scalars(
            select(HolidayCalendarRow).order_by(HolidayCalendarRow.name, HolidayCalendarRow.slug)
        )
        return [_calendar(row) for row in rows]

    async def get_holiday_calendar(self, slug: str) -> HolidayCalendar | None:
        """Return the holiday calendar with ``slug``."""
        row = await self._session.scalar(
            select(HolidayCalendarRow).where(HolidayCalendarRow.slug == slug)
        )
        return _calendar(row) if row else None

    async def add_holiday_calendar(self, calendar: HolidayCalendar) -> HolidayCalendar:
        """Insert a calendar and return it with its ID."""
        row = HolidayCalendarRow(slug=calendar.slug, name=calendar.name)
        self._session.add(row)
        await self._session.flush()
        return _calendar(row)

    async def delete_holiday_calendar(self, calendar_id: int) -> None:
        """Delete the calendar; its holidays go with it (``ON DELETE CASCADE``)."""
        await self._session.execute(
            delete(HolidayCalendarRow).where(HolidayCalendarRow.id == calendar_id)
        )

    async def count_holiday_calendar_users(self, calendar_id: int) -> int:
        """Return how many organisations and groups chose the calendar."""
        organisations = await self._session.scalar(
            select(func.count()).where(OrganisationHolidayCalendarRow.calendar_id == calendar_id)
        )
        groups = await self._session.scalar(
            select(func.count()).where(GroupHolidayCalendarRow.calendar_id == calendar_id)
        )
        return int(organisations or 0) + int(groups or 0)

    async def get_holidays(
        self, calendar_ids: Collection[int], first_day: date, last_day: date
    ) -> list[Holiday]:
        """Return the holidays of the calendars between the two dates, by date."""
        rows = await self._session.scalars(
            select(HolidayRow)
            .where(
                HolidayRow.calendar_id.in_(sorted(calendar_ids)),
                HolidayRow.day.between(first_day, last_day),
            )
            .order_by(HolidayRow.day, HolidayRow.calendar_id)
        )
        return [_holiday(row) for row in rows]

    async def add_holiday(self, holiday: Holiday) -> int:
        """Insert a holiday and return its ID."""
        row = HolidayRow(calendar_id=holiday.calendar_id, day=holiday.day, name=holiday.name)
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def delete_holiday(self, calendar_id: int, holiday_id: int) -> bool:
        """Delete the calendar's holiday; the calendar condition keeps others out of reach."""
        result = await self._session.execute(
            delete(HolidayRow).where(
                HolidayRow.id == holiday_id, HolidayRow.calendar_id == calendar_id
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def get_organisation_holiday_calendars(self, organisation_id: int) -> frozenset[int]:
        """Return the ids of the calendars the organisation chose."""
        ids = await self._session.scalars(
            select(OrganisationHolidayCalendarRow.calendar_id).where(
                OrganisationHolidayCalendarRow.organisation_id == organisation_id
            )
        )
        return frozenset(ids)

    async def save_organisation_holiday_calendars(
        self, organisation_id: int, calendar_ids: frozenset[int]
    ) -> None:
        """Replace the organisation's calendar choice."""
        await self._session.execute(
            delete(OrganisationHolidayCalendarRow).where(
                OrganisationHolidayCalendarRow.organisation_id == organisation_id
            )
        )
        self._session.add_all(
            OrganisationHolidayCalendarRow(organisation_id=organisation_id, calendar_id=c)
            for c in sorted(calendar_ids)
        )
        await self._session.flush()

    async def get_group_holidays(self, group_id: int) -> GroupHolidays:
        """Return the group's source, own calendars and the holidays it keeps open."""
        source = await self._session.scalar(
            select(GroupRow.holiday_source).where(GroupRow.id == group_id)
        )
        calendar_ids = await self._session.scalars(
            select(GroupHolidayCalendarRow.calendar_id).where(
                GroupHolidayCalendarRow.group_id == group_id
            )
        )
        open_days = await self._session.scalars(
            select(GroupHolidayOpenDayRow.day).where(GroupHolidayOpenDayRow.group_id == group_id)
        )
        return GroupHolidays(
            HolidaySource(source or HolidaySource.INHERIT.value),
            frozenset(calendar_ids),
            frozenset(open_days),
        )

    async def save_group_holidays(self, group_id: int, holidays: GroupHolidays) -> None:
        """Replace the group's holiday choice."""
        await self._session.execute(
            update(GroupRow)
            .where(GroupRow.id == group_id)
            .values(holiday_source=holidays.source.value)
        )
        for table in (GroupHolidayCalendarRow, GroupHolidayOpenDayRow):
            await self._session.execute(delete(table).where(table.group_id == group_id))
        self._session.add_all(
            GroupHolidayCalendarRow(group_id=group_id, calendar_id=c)
            for c in sorted(holidays.calendar_ids)
        )
        self._session.add_all(
            GroupHolidayOpenDayRow(group_id=group_id, day=d) for d in sorted(holidays.open_days)
        )
        await self._session.flush()

    async def save_meeting(self, booking_id: int, meeting: MeetingRef) -> None:
        """Store the booking's meeting id and join link (one link; roles follow the account)."""
        await self._session.execute(
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(
                video_room_id=meeting.id,
                guest_join_url=meeting.link,
                host_join_url=meeting.link,
            )
        )

    async def save_task_id(self, booking_id: int, task_id: str) -> None:
        """Store the booking's WxCC task id."""
        await self._session.execute(
            update(BookingRow).where(BookingRow.id == booking_id).values(wxcc_task_id=task_id)
        )

    async def get_booking_by_meeting(self, meeting_id: str) -> Booking | None:
        """Return the booking whose meeting has ``meeting_id``."""
        return await self._booking_where(BookingRow.video_room_id == meeting_id)

    async def get_booking_by_task(self, task_id: str) -> Booking | None:
        """Return the booking whose WxCC task has ``task_id``."""
        return await self._booking_where(BookingRow.wxcc_task_id == task_id)

    async def _booking_where(self, condition: ColumnElement[bool]) -> Booking | None:
        """Return the one booking matching ``condition``, with its slot."""
        found = (
            await self._session.execute(
                select(BookingRow, SlotRow)
                .join(SlotRow, SlotRow.id == BookingRow.slot_id)
                .where(condition)
                .limit(1)
            )
        ).one_or_none()
        return _booking(*found) if found else None

    async def claim_webhook_event(self, source: str, key: str, at: datetime) -> bool:
        """Insert the event key unless it exists; ``True`` when it was new."""
        result = await self._session.execute(
            sqlite_insert(WebhookEventRow)
            .values(source=source, event_key=key, seen_at=at)
            .on_conflict_do_nothing()
        )
        return bool(getattr(result, "rowcount", 0))

    async def forget_webhook_events(self, before: datetime) -> int:
        """Delete event keys seen before ``before``."""
        result = await self._session.execute(
            delete(WebhookEventRow).where(WebhookEventRow.seen_at < before)
        )
        return int(getattr(result, "rowcount", 0))

    async def add_booking_event(self, event: BookingEvent) -> None:
        """Append an event to a booking's timeline."""
        self._session.add(
            BookingEventRow(
                booking_id=event.booking_id,
                at=event.at,
                kind=event.kind.value,
                detail=event.detail[:128],
            )
        )
        await self._session.flush()

    async def get_booking_events(self, booking_id: int) -> list[BookingEvent]:
        """Return a booking's timeline, oldest first."""
        rows = await self._session.scalars(
            select(BookingEventRow)
            .where(BookingEventRow.booking_id == booking_id)
            .order_by(BookingEventRow.at, BookingEventRow.id)
        )
        return [BookingEvent(r.booking_id, r.at, EventKind(r.kind), r.detail) for r in rows]

    async def list_bookings(
        self, scope: Scope, group_id: int, lower: datetime, upper: datetime
    ) -> list[BookingOverview]:
        """Return the group's bookings in [lower, upper) with their status, inside ``scope``."""
        rows = await self._session.execute(
            select(BookingRow, SlotRow, CallStatusRow)
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .join(GroupRow, GroupRow.id == BookingRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .outerjoin(CallStatusRow, CallStatusRow.booking_id == BookingRow.id)
            .where(
                BookingRow.group_id == group_id,
                SlotRow.start >= lower,
                SlotRow.start < upper,
                scope_filter(scope, OrganisationRow.slug, GroupRow.slug),
            )
            .order_by(SlotRow.start, BookingRow.id)
        )
        return [
            BookingOverview(
                _booking(booking, slot),
                _call_status(status) if status else new_status(booking.id),
            )
            for booking, slot, status in rows
        ]

    async def get_call_status(self, booking_id: int) -> CallStatus | None:
        """Return the booking's aggregated call status."""
        row = await self._session.get(CallStatusRow, booking_id)
        return _call_status(row) if row else None

    async def save_call_status(self, status: CallStatus) -> None:
        """Create or replace the booking's aggregated call status."""
        await self._session.merge(
            CallStatusRow(
                booking_id=status.booking_id,
                call=status.call.value,
                task=status.task.value,
                participants=status.participants,
                host_joined=status.host_joined,
                host_joined_at=status.host_joined_at,
                started_at=status.started_at,
                ended_at=status.ended_at,
                task_created_at=status.task_created_at,
                connected_at=status.connected_at,
                updated_at=status.updated_at,
            )
        )
        await self._session.flush()

    async def get_reminder_rules(self, group_id: int) -> list[ReminderRule]:
        """Return the group's reminder rules."""
        rows = await self._session.scalars(
            select(ReminderRuleRow).where(ReminderRuleRow.group_id == group_id)
        )
        return [ReminderRule(ChannelKind(r.channel), r.minutes_before, r.enabled) for r in rows]

    async def add_reminder_rule(self, group_id: int, rule: ReminderRule) -> None:
        """Insert a reminder rule (the unique constraint backs the domain's duplicate check)."""
        self._session.add(
            ReminderRuleRow(
                group_id=group_id,
                channel=rule.channel.value,
                minutes_before=rule.minutes_before,
                enabled=rule.enabled,
            )
        )
        await self._session.flush()

    async def delete_reminder_rule(
        self, group_id: int, channel: ChannelKind, minutes_before: int
    ) -> bool:
        """Delete the group's rule with this channel and offset."""
        result = await self._session.execute(
            delete(ReminderRuleRow).where(
                ReminderRuleRow.group_id == group_id,
                ReminderRuleRow.channel == channel.value,
                ReminderRuleRow.minutes_before == minutes_before,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def get_channel_settings(self, organisation_id: int) -> list[ChannelSettings]:
        """Return the organisation's channels; only whether a secret is set, never its value."""
        rows = await self._session.scalars(
            select(ChannelSettingsRow)
            .where(ChannelSettingsRow.organisation_id == organisation_id)
            .order_by(ChannelSettingsRow.channel)
        )
        return [
            ChannelSettings(
                _channel_config(ChannelKind(row.channel), row.settings),
                row.secret_ciphertext is not None,
                row.updated_at,
            )
            for row in rows
        ]

    async def save_channel_settings(
        self, organisation_id: int, config: ChannelConfig, secret: str | None, now: datetime
    ) -> None:
        """Create or replace the channel's settings; seal a new secret, else keep the old one.

        Raises:
            DomainError: ``integration_not_ready`` when a secret is given but no settings key
                is configured (``SETTINGS_ENCRYPTION_KEY``).
        """
        row = await self._session.get(ChannelSettingsRow, (organisation_id, config.channel.value))
        if row is None:
            row = ChannelSettingsRow(organisation_id=organisation_id, channel=config.channel.value)
            self._session.add(row)
        row.settings = json.dumps(asdict(config), sort_keys=True)
        row.updated_at = now
        if secret is not None:
            if self._box is None:
                raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "settings_key")
            row.secret_nonce, row.secret_ciphertext = self._box.seal(
                _secret_name(organisation_id, config.channel), secret.encode()
            )
        await self._session.flush()

    async def delete_channel_settings(self, organisation_id: int, channel: ChannelKind) -> bool:
        """Delete the channel's settings and its sealed secret."""
        result = await self._session.execute(
            delete(ChannelSettingsRow).where(
                ChannelSettingsRow.organisation_id == organisation_id,
                ChannelSettingsRow.channel == channel.value,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def get_channel_secret(self, organisation_id: int, channel: ChannelKind) -> str | None:
        """Return the channel's decrypted secret, for building its sender in the worker.

        Not part of the ``Transaction`` port: the domain never reads a secret back (ADR 0008).

        Raises:
            DomainError: ``integration_not_ready`` without a settings key.
        """
        row = await self._session.get(ChannelSettingsRow, (organisation_id, channel.value))
        if row is None or row.secret_nonce is None or row.secret_ciphertext is None:
            return None
        if self._box is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "settings_key")
        name = _secret_name(organisation_id, channel)
        return self._box.open(name, row.secret_nonce, row.secret_ciphertext).decode()

    async def get_group_texts(self, group_id: int) -> dict[tuple[str, str], str]:
        """Return the group's own message texts by ``(locale, block key)``."""
        rows = await self._session.scalars(
            select(GroupTextRow).where(GroupTextRow.group_id == group_id)
        )
        return {(row.locale, row.key): row.text for row in rows}

    async def save_group_text(self, group_id: int, locale: str, key: str, text: str | None) -> None:
        """Store or, with ``None``, delete the group's text of a block in a locale."""
        if text is None:
            await self._session.execute(
                delete(GroupTextRow).where(
                    GroupTextRow.group_id == group_id,
                    GroupTextRow.locale == locale,
                    GroupTextRow.key == key,
                )
            )
            return
        await self._session.merge(
            GroupTextRow(group_id=group_id, locale=locale, key=key, text=text)
        )
        await self._session.flush()

    async def get_slot_usage(
        self, group_id: int, lower: datetime, upper: datetime
    ) -> list[SlotUsage]:
        """Return capacity and the bookings holding a place in existing slots, in one query."""
        active = func.count(case((BookingRow.status.in_(_HOLDS_PLACE), 1)))
        rows = await self._session.execute(
            select(SlotRow.start, SlotRow.capacity_snapshot, active)
            .outerjoin(BookingRow, BookingRow.slot_id == SlotRow.id)
            .where(
                SlotRow.group_id == group_id,
                SlotRow.start >= lower,
                SlotRow.start < upper,
                SlotRow.kind == BookingKind.SCHEDULED.value,
            )
            .group_by(SlotRow.id)
        )
        return [SlotUsage(start, capacity, int(count)) for start, capacity, count in rows]

    async def get_slot(self, group_id: int, start: datetime) -> Slot | None:
        """Return the existing slot of the group at ``start``."""
        row = await self._session.scalar(
            select(SlotRow).where(
                SlotRow.group_id == group_id,
                SlotRow.start == start,
                SlotRow.kind == BookingKind.SCHEDULED.value,
            )
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
            kind=slot.kind.value,
        )
        self._session.add(row)
        await self._session.flush()
        return Slot(row.id, slot.group_id, slot.start, slot.end, slot.capacity_snapshot, slot.kind)

    async def count_active_bookings(self, slot_id: int) -> int:
        """Return the number of bookings holding a place in the slot (confirmed or on hold)."""
        count = await self._session.scalar(
            select(func.count()).where(
                BookingRow.slot_id == slot_id,
                BookingRow.status.in_(_HOLDS_PLACE),
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
        """Persist the mutable fields of a booking (status, cancellation time, closure)."""
        await self._session.execute(
            update(BookingRow)
            .where(BookingRow.id == booking.id)
            .values(
                status=booking.status.value,
                cancelled_at=booking.cancelled_at,
                closure_id=booking.closure_id,
            )
        )

    async def replace_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        """Delete the booking's pending jobs and insert ``jobs``."""
        await self._session.execute(
            delete(JobRow).where(JobRow.booking_id == booking_id, JobRow.status == _PENDING)
        )
        await self.add_jobs(booking_id, jobs)

    async def add_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        """Insert ``jobs`` for the booking next to its pending jobs."""
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

    async def delete_pending_jobs(self, booking_id: int, types: Collection[JobType]) -> None:
        """Delete the booking's pending jobs of the given types."""
        await self._session.execute(
            delete(JobRow).where(
                JobRow.booking_id == booking_id,
                JobRow.status == _PENDING,
                JobRow.type.in_(sorted(t.value for t in types)),
            )
        )

    async def get_closures(self, group_id: int, now: datetime) -> list[Closure]:
        """Return the group's closures that have not ended at ``now``, earliest first."""
        rows = await self._session.scalars(
            select(ClosureRow)
            .where(
                ClosureRow.group_id == group_id,
                or_(ClosureRow.ends_at.is_(None), ClosureRow.ends_at > now),
            )
            .order_by(ClosureRow.starts_at, ClosureRow.id)
        )
        return [await self._with_notices(row) for row in rows]

    async def get_closure(self, closure_id: int) -> Closure | None:
        """Return the closure with ``closure_id``."""
        row = await self._session.get(ClosureRow, closure_id)
        return await self._with_notices(row) if row else None

    async def add_closure(self, closure: Closure) -> Closure:
        """Insert a closure with its notices and return it with its id."""
        row = ClosureRow(
            group_id=closure.group_id,
            starts_at=closure.starts_at,
            ends_at=closure.ends_at,
            created_at=closure.created_at,
        )
        self._session.add(row)
        await self._session.flush()
        await self._save_notices(row.id, closure)
        return await self._with_notices(row)

    async def save_closure(self, closure: Closure) -> None:
        """Store the window and the notices of an existing closure."""
        await self._session.execute(
            update(ClosureRow)
            .where(ClosureRow.id == closure.id)
            .values(starts_at=closure.starts_at, ends_at=closure.ends_at)
        )
        if closure.id is not None:
            await self._save_notices(closure.id, closure)

    async def find_confirmed_bookings(
        self, group_id: int, lower: datetime, upper: datetime | None
    ) -> list[Booking]:
        """Return the group's confirmed scheduled bookings with lower <= start < upper."""
        query = (
            select(BookingRow, SlotRow)
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .where(
                BookingRow.group_id == group_id,
                BookingRow.status == BookingStatus.CONFIRMED.value,
                SlotRow.kind == BookingKind.SCHEDULED.value,
                SlotRow.start >= lower,
            )
        )
        if upper is not None:
            query = query.where(SlotRow.start < upper)
        rows = await self._session.execute(query.order_by(SlotRow.start, BookingRow.id))
        return [_booking(booking, slot) for booking, slot in rows]

    async def list_suspended_bookings(self, scope: Scope, group_id: int) -> list[Booking]:
        """Return the group's suspended bookings, earliest first, inside ``scope``."""
        rows = await self._session.execute(
            select(BookingRow, SlotRow)
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .join(GroupRow, GroupRow.id == BookingRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(
                BookingRow.group_id == group_id,
                BookingRow.status == BookingStatus.SUSPENDED.value,
                scope_filter(scope, OrganisationRow.slug, GroupRow.slug),
            )
            .order_by(SlotRow.start, BookingRow.id)
        )
        return [_booking(booking, slot) for booking, slot in rows]

    async def _with_notices(self, row: ClosureRow) -> Closure:
        """Return the closure of ``row`` with its notices."""
        notices = await self._session.scalars(
            select(ClosureNoticeRow).where(ClosureNoticeRow.closure_id == row.id)
        )
        return _closure(row, list(notices))

    async def _save_notices(self, closure_id: int, closure: Closure) -> None:
        """Replace the notices of ``closure_id`` with those of ``closure``."""
        await self._session.execute(
            delete(ClosureNoticeRow).where(ClosureNoticeRow.closure_id == closure_id)
        )
        self._session.add_all(
            ClosureNoticeRow(closure_id=closure_id, locale=locale, text=text)
            for locale, text in closure.notices.items()
        )
        await self._session.flush()

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
        box: Seals channel secrets (ADR 0008); without it they cannot be stored or read.
    """

    def __init__(self, engine: AsyncEngine, box: SecretBox | None = None) -> None:
        """Store the engine and the secret box."""
        self._engine = engine
        self._box = box

    @asynccontextmanager
    async def read(self) -> AsyncIterator[SqlTransaction]:
        """Open a read transaction (deferred ``BEGIN``)."""
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            async with session.begin():
                yield SqlTransaction(session, self._box)

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[SqlTransaction]:
        """Open a write transaction with ``BEGIN IMMEDIATE``; commit on success.

        Raises:
            DomainError: ``database_busy`` if the write lock is not obtained in time.
        """
        async with locked_session(self._engine) as session:
            yield SqlTransaction(session, self._box)


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
