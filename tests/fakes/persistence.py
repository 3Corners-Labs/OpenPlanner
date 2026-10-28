"""In-memory UnitOfWork with commit/rollback semantics, for domain service tests."""

import asyncio
import copy
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from app.domain import holidays as holiday_model
from app.domain.api_clients import AccessToken, ApiClient, ApiSettings
from app.domain.audit import AdminProfile, AuditEntry, AuditEvent, AuditFilter, Source
from app.domain.authorization import Scope
from app.domain.booking import Booking, BookingKind, BookingStatus, MeetingRef, Slot, SlotUsage
from app.domain.branding import Branding, Logo
from app.domain.calls import BookingEvent, BookingOverview, CallStatus, new_status
from app.domain.channel_settings import ChannelConfig, ChannelSettings
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.checks import GroupCheck
from app.domain.closure import Closure
from app.domain.direct_queue import DirectCall
from app.domain.job_plan import PlannedJob
from app.domain.retention import GroupRetentionCounts, PurgeCandidate, RetentionFacts
from app.domain.scheduling import (
    Group,
    GroupSummary,
    Organisation,
    ScheduleOverride,
    ScheduleRule,
)
from app.domain.updates import ReleaseCheck, UpdateRequest


@dataclass
class State:
    groups: dict[int, Group] = field(default_factory=dict)
    rules: dict[int, list[ScheduleRule]] = field(default_factory=dict)
    overrides: dict[int, list[ScheduleOverride]] = field(default_factory=dict)
    reminder_rules: dict[int, list[ReminderRule]] = field(default_factory=dict)
    slots: dict[int, Slot] = field(default_factory=dict)
    bookings: dict[int, Booking] = field(default_factory=dict)
    jobs: dict[int, list[PlannedJob]] = field(default_factory=dict)
    organisation_slugs: dict[int, str] = field(default_factory=dict)
    organisations: dict[int, Organisation] = field(default_factory=dict)
    booking_events: dict[int, list[BookingEvent]] = field(default_factory=dict)
    call_status: dict[int, CallStatus] = field(default_factory=dict)
    webhook_events: dict[tuple[str, str], datetime] = field(default_factory=dict)
    audit: list[AuditEvent] = field(default_factory=list)
    admin_profiles: dict[str, AdminProfile] = field(default_factory=dict)
    logos: dict[int, Logo] = field(default_factory=dict)
    group_checks: dict[int, GroupCheck] = field(default_factory=dict)
    holiday_calendars: dict[int, holiday_model.HolidayCalendar] = field(default_factory=dict)
    holidays: dict[int, holiday_model.Holiday] = field(default_factory=dict)
    organisation_holidays: dict[int, frozenset[int]] = field(default_factory=dict)
    group_holidays: dict[int, holiday_model.GroupHolidays] = field(default_factory=dict)
    closures: dict[int, Closure] = field(default_factory=dict)
    api_settings: ApiSettings = field(default_factory=ApiSettings)
    api_clients: dict[str, ApiClient] = field(default_factory=dict)
    access_tokens: dict[str, AccessToken] = field(default_factory=dict)
    channel_settings: dict[tuple[int, ChannelKind], ChannelSettings] = field(default_factory=dict)
    channel_secrets: dict[tuple[int, ChannelKind], str] = field(default_factory=dict)
    group_texts: dict[int, dict[tuple[str, str], str]] = field(default_factory=dict)
    release_check: ReleaseCheck | None = None
    update_requests: dict[int, UpdateRequest] = field(default_factory=dict)
    purged_at: dict[int, datetime] = field(default_factory=dict)
    failed_purges: set[int] = field(default_factory=set)
    next_id: int = 1


class InMemoryTransaction:
    def __init__(self, state: State) -> None:
        self.state = state

    def _new_id(self) -> int:
        self.state.next_id += 1
        return self.state.next_id

    def _in_scope(self, scope: Scope, group_id: int) -> bool:
        group = self.state.groups.get(group_id)
        return group is not None and scope.contains(
            self._summary(group).organisation_slug, group.slug
        )

    def _summary(self, group: Group) -> GroupSummary:
        slug = self.state.organisation_slugs.get(group.organisation_id, "org")
        return GroupSummary(group, slug, slug.title())

    async def list_groups(self, scope: Scope) -> list[GroupSummary]:
        summaries = [self._summary(g) for g in self.state.groups.values()]
        return [s for s in summaries if scope.contains(s.organisation_slug, s.group.slug)]

    async def get_group_summary(self, slug: str) -> GroupSummary | None:
        group = await self.get_group(slug)
        return self._summary(group) if group else None

    async def add_audit_event(self, event: AuditEvent) -> None:
        self.state.audit.append(event)

    async def list_audit_events(
        self, scope: Scope, limit: int = 100, narrow: AuditFilter | None = None
    ) -> list[AuditEntry]:
        narrow = narrow or AuditFilter()
        events = [
            e
            for e in self.state.audit
            if scope.contains(e.organisation, e.group) and narrow.matches(e)
        ]
        profiles = self.state.admin_profiles
        return [
            AuditEntry(e, profiles[e.actor].display_name if e.actor in profiles else None)
            for e in reversed(events)
        ][:limit]

    async def delete_audit_events(self, source: Source, before: datetime) -> int:
        kept = [e for e in self.state.audit if e.source is not source or e.at >= before]
        removed = len(self.state.audit) - len(kept)
        self.state.audit = kept
        return removed

    async def save_admin_profile(self, profile: AdminProfile) -> None:
        self.state.admin_profiles[profile.subject] = profile

    async def get_group(self, slug: str) -> Group | None:
        return next((g for g in self.state.groups.values() if g.slug == slug), None)

    async def get_group_by_id(self, group_id: int) -> Group | None:
        return self.state.groups.get(group_id)

    async def get_schedule(
        self, group_id: int, first_day: date, last_day: date
    ) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
        overrides = [
            o for o in self.state.overrides.get(group_id, []) if first_day <= o.day <= last_day
        ]
        return list(self.state.rules.get(group_id, [])), overrides

    async def list_organisations(self, scope: Scope) -> list[Organisation]:
        found = [o for o in self.state.organisations.values() if scope.contains(o.slug, None)]
        return sorted(found, key=lambda o: o.slug)

    async def get_organisation(self, slug: str) -> Organisation | None:
        return next((o for o in self.state.organisations.values() if o.slug == slug), None)

    async def save_group_check(self, check: GroupCheck) -> None:
        self.state.group_checks[check.group_id] = check

    async def get_group_check(self, group_id: int) -> GroupCheck | None:
        return self.state.group_checks.get(group_id)

    async def get_organisation_by_id(self, organisation_id: int) -> Organisation | None:
        return self.state.organisations.get(organisation_id)

    async def add_organisation(self, organisation: Organisation) -> Organisation:
        stored = replace(organisation, id=self._new_id())
        self.state.organisations[stored.id] = stored
        self.state.organisation_slugs[stored.id] = stored.slug
        return stored

    async def save_organisation(self, organisation: Organisation) -> None:
        current = self.state.organisations[organisation.id]
        self.state.organisations[organisation.id] = replace(
            current, name=organisation.name, default_locale=organisation.default_locale
        )

    async def save_branding(self, organisation_id: int, branding: Branding) -> None:
        organisation = self.state.organisations[organisation_id]
        self.state.organisations[organisation_id] = replace(organisation, branding=branding)

    async def get_logo(self, organisation_slug: str) -> Logo | None:
        organisation = await self.get_organisation(organisation_slug)
        return self.state.logos.get(organisation.id) if organisation else None

    async def save_logo(self, organisation_id: int, logo: Logo | None) -> None:
        organisation = self.state.organisations[organisation_id]
        if logo is None:
            self.state.logos.pop(organisation_id, None)
        else:
            self.state.logos[organisation_id] = logo
        branding = replace(organisation.branding, logo=logo.ref if logo else None)
        self.state.organisations[organisation_id] = replace(organisation, branding=branding)

    async def add_group(self, group: Group) -> Group:
        stored = replace(group, id=self._new_id())
        self.state.groups[stored.id] = stored
        return stored

    async def save_group(self, group: Group) -> None:
        self.state.groups[group.id] = group

    async def count_future_bookings(self, group_id: int, now: datetime) -> int:
        return sum(
            1
            for b in self.state.bookings.values()
            if b.group_id == group_id and b.holds_place and b.start > now
        )

    async def get_schedule_items(
        self, group_id: int, from_day: date | None = None
    ) -> tuple[list[ScheduleRule], list[ScheduleOverride]]:
        overrides = [
            o
            for o in self.state.overrides.get(group_id, [])
            if from_day is None or o.day >= from_day
        ]
        return list(self.state.rules.get(group_id, [])), overrides

    async def add_schedule_rule(self, group_id: int, rule: ScheduleRule) -> int:
        stored = replace(rule, id=self._new_id())
        self.state.rules.setdefault(group_id, []).append(stored)
        return stored.id or 0

    async def update_schedule_rule(self, group_id: int, rule_id: int, rule: ScheduleRule) -> bool:
        rules = self.state.rules.get(group_id, [])
        found = any(r.id == rule_id for r in rules)
        self.state.rules[group_id] = [
            replace(rule, id=rule_id) if r.id == rule_id else r for r in rules
        ]
        return found

    async def delete_schedule_rule(self, group_id: int, rule_id: int) -> bool:
        rules = self.state.rules.get(group_id, [])
        kept = [r for r in rules if r.id != rule_id]
        self.state.rules[group_id] = kept
        return len(kept) != len(rules)

    async def add_schedule_override(self, group_id: int, override: ScheduleOverride) -> int:
        stored = replace(override, id=self._new_id())
        self.state.overrides.setdefault(group_id, []).append(stored)
        return stored.id or 0

    async def delete_schedule_override(self, group_id: int, override_id: int) -> bool:
        overrides = self.state.overrides.get(group_id, [])
        kept = [o for o in overrides if o.id != override_id]
        self.state.overrides[group_id] = kept
        return len(kept) != len(overrides)

    async def get_holiday_calendars(self) -> list[holiday_model.HolidayCalendar]:
        return sorted(self.state.holiday_calendars.values(), key=lambda c: c.name)

    async def get_holiday_calendar(self, slug: str) -> holiday_model.HolidayCalendar | None:
        return next((c for c in self.state.holiday_calendars.values() if c.slug == slug), None)

    async def add_holiday_calendar(
        self, calendar: holiday_model.HolidayCalendar
    ) -> holiday_model.HolidayCalendar:
        stored = replace(calendar, id=self._new_id())
        self.state.holiday_calendars[stored.id] = stored
        return stored

    async def delete_holiday_calendar(self, calendar_id: int) -> None:
        del self.state.holiday_calendars[calendar_id]
        self.state.holidays = {
            k: h for k, h in self.state.holidays.items() if h.calendar_id != calendar_id
        }

    async def count_holiday_calendar_users(self, calendar_id: int) -> int:
        organisations = sum(calendar_id in ids for ids in self.state.organisation_holidays.values())
        groups = sum(calendar_id in g.calendar_ids for g in self.state.group_holidays.values())
        return organisations + groups

    async def get_holidays(
        self, calendar_ids: Collection[int], first_day: date, last_day: date
    ) -> list[holiday_model.Holiday]:
        found = [
            h
            for h in self.state.holidays.values()
            if h.calendar_id in calendar_ids and first_day <= h.day <= last_day
        ]
        return sorted(found, key=lambda h: (h.day, h.calendar_id))

    async def add_holiday(self, holiday: holiday_model.Holiday) -> int:
        stored = replace(holiday, id=self._new_id())
        self.state.holidays[stored.id or 0] = stored
        return stored.id or 0

    async def delete_holiday(self, calendar_id: int, holiday_id: int) -> bool:
        holiday = self.state.holidays.get(holiday_id)
        if holiday is None or holiday.calendar_id != calendar_id:
            return False
        del self.state.holidays[holiday_id]
        return True

    async def get_organisation_holiday_calendars(self, organisation_id: int) -> frozenset[int]:
        return self.state.organisation_holidays.get(organisation_id, frozenset())

    async def save_organisation_holiday_calendars(
        self, organisation_id: int, calendar_ids: frozenset[int]
    ) -> None:
        self.state.organisation_holidays[organisation_id] = calendar_ids

    async def get_group_holidays(self, group_id: int) -> holiday_model.GroupHolidays:
        return self.state.group_holidays.get(group_id, holiday_model.GroupHolidays())

    async def save_group_holidays(
        self, group_id: int, holidays: holiday_model.GroupHolidays
    ) -> None:
        self.state.group_holidays[group_id] = holidays

    async def save_meeting(self, booking_id: int, meeting: MeetingRef) -> None:
        self.state.bookings[booking_id] = replace(self.state.bookings[booking_id], meeting=meeting)

    async def save_task_id(self, booking_id: int, task_id: str) -> None:
        booking = self.state.bookings[booking_id]
        self.state.bookings[booking_id] = replace(booking, wxcc_task_id=task_id)

    async def get_booking_by_meeting(self, meeting_id: str) -> Booking | None:
        return next(
            (b for b in self.state.bookings.values() if b.meeting and b.meeting.id == meeting_id),
            None,
        )

    async def get_booking_by_task(self, task_id: str) -> Booking | None:
        return next((b for b in self.state.bookings.values() if b.wxcc_task_id == task_id), None)

    async def claim_webhook_event(self, source: str, key: str, at: datetime) -> bool:
        if (source, key) in self.state.webhook_events:
            return False
        self.state.webhook_events[(source, key)] = at
        return True

    async def forget_webhook_events(self, before: datetime) -> int:
        old = [k for k, at in self.state.webhook_events.items() if at < before]
        for key in old:
            del self.state.webhook_events[key]
        return len(old)

    async def add_booking_event(self, event: BookingEvent) -> None:
        self.state.booking_events.setdefault(event.booking_id, []).append(event)

    async def get_booking_events(self, booking_id: int) -> list[BookingEvent]:
        return list(self.state.booking_events.get(booking_id, []))

    async def list_bookings(
        self, scope: Scope, group_id: int, lower: datetime, upper: datetime
    ) -> list[BookingOverview]:
        if not self._in_scope(scope, group_id):
            return []
        found = [
            b
            for b in self.state.bookings.values()
            if b.group_id == group_id and lower <= b.start < upper
        ]
        return [
            BookingOverview(b, self.state.call_status.get(b.id or 0) or new_status(b.id or 0))
            for b in sorted(found, key=lambda b: (b.start, b.id or 0))
        ]

    async def get_direct_calls(self, group_id: int, since: datetime) -> list[DirectCall]:
        return [
            DirectCall(
                b.id or 0,
                b.start,
                b.end,
                b.is_active,
                self.state.call_status.get(b.id or 0) or new_status(b.id or 0),
            )
            for b in self.state.bookings.values()
            if b.group_id == group_id and b.kind is BookingKind.DIRECT and b.start >= since
        ]

    async def get_call_status(self, booking_id: int) -> CallStatus | None:
        return self.state.call_status.get(booking_id)

    async def save_call_status(self, status: CallStatus) -> None:
        self.state.call_status[status.booking_id] = status

    async def get_reminder_rules(self, group_id: int) -> list[ReminderRule]:
        return list(self.state.reminder_rules.get(group_id, []))

    async def add_reminder_rule(self, group_id: int, rule: ReminderRule) -> None:
        self.state.reminder_rules.setdefault(group_id, []).append(rule)

    async def delete_reminder_rule(
        self, group_id: int, channel: ChannelKind, minutes_before: int
    ) -> bool:
        rules = self.state.reminder_rules.get(group_id, [])
        kept = [r for r in rules if (r.channel, r.minutes_before) != (channel, minutes_before)]
        self.state.reminder_rules[group_id] = kept
        return len(kept) != len(rules)

    async def get_channel_settings(self, organisation_id: int) -> list[ChannelSettings]:
        return [s for (org, _), s in self.state.channel_settings.items() if org == organisation_id]

    async def save_channel_settings(
        self, organisation_id: int, config: ChannelConfig, secret: str | None, now: datetime
    ) -> None:
        key = (organisation_id, config.channel)
        if secret is not None:
            self.state.channel_secrets[key] = secret
        self.state.channel_settings[key] = ChannelSettings(
            config, key in self.state.channel_secrets, now
        )

    async def delete_channel_settings(self, organisation_id: int, channel: ChannelKind) -> bool:
        self.state.channel_secrets.pop((organisation_id, channel), None)
        return self.state.channel_settings.pop((organisation_id, channel), None) is not None

    async def get_group_texts(self, group_id: int) -> dict[tuple[str, str], str]:
        return dict(self.state.group_texts.get(group_id, {}))

    async def save_group_text(self, group_id: int, locale: str, key: str, text: str | None) -> None:
        texts = self.state.group_texts.setdefault(group_id, {})
        if text is None:
            texts.pop((locale, key), None)
        else:
            texts[(locale, key)] = text

    async def get_slot_usage(
        self, group_id: int, lower: datetime, upper: datetime
    ) -> list[SlotUsage]:
        return [
            SlotUsage(s.start, s.capacity_snapshot, await self.count_active_bookings(s.id or 0))
            for s in self.state.slots.values()
            if s.group_id == group_id and lower <= s.start < upper and s.kind == "scheduled"
        ]

    async def get_slot(self, group_id: int, start: datetime) -> Slot | None:
        return next(
            (
                s
                for s in self.state.slots.values()
                if s.group_id == group_id and s.start == start and s.kind == "scheduled"
            ),
            None,
        )

    async def add_slot(self, slot: Slot) -> Slot:
        stored = replace(slot, id=self._new_id())
        self.state.slots[stored.id or 0] = stored
        return stored

    async def count_active_bookings(self, slot_id: int) -> int:
        return sum(
            1 for b in self.state.bookings.values() if b.slot_id == slot_id and b.holds_place
        )

    async def add_booking(self, booking: Booking) -> Booking:
        stored = replace(booking, id=self._new_id())
        self.state.bookings[stored.id or 0] = stored
        return stored

    async def get_booking(self, booking_id: int) -> Booking | None:
        return self.state.bookings.get(booking_id)

    async def purge_personal_data(self, booking_id: int, now: datetime) -> None:
        self.state.bookings[booking_id] = replace(self.state.bookings[booking_id], contact=None)
        self.state.purged_at[booking_id] = now

    async def list_retention(self, scope: Scope, candidates_before: datetime) -> RetentionFacts:
        groups = await self.list_groups(scope)
        counts, candidates = [], []
        for summary in groups:
            bookings = [b for b in self.state.bookings.values() if b.group_id == summary.group.id]
            holding = [b for b in bookings if b.contact is not None]
            purged = [self.state.purged_at[b.id] for b in bookings if b.id in self.state.purged_at]
            failed = sum(1 for b in holding if b.id in self.state.failed_purges)
            counts.append(
                GroupRetentionCounts(summary, len(holding), failed, max(purged, default=None))
            )
            for b in holding:
                closure = self.state.closures.get(b.closure_id) if b.closure_id else None
                if (
                    b.end < candidates_before
                    or (b.cancelled_at is not None and b.cancelled_at < candidates_before)
                    or b.status is BookingStatus.SUSPENDED
                ):
                    ends = closure.ends_at if closure else None
                    candidates.append(
                        PurgeCandidate(b.group_id, b.status, b.end, b.cancelled_at, ends)
                    )
        return RetentionFacts(counts, candidates)

    async def get_booking_by_token_hash(self, token_hash: str) -> Booking | None:
        return next((b for b in self.state.bookings.values() if b.token_hash == token_hash), None)

    async def save_booking(self, booking: Booking) -> None:
        self.state.bookings[booking.id or 0] = booking

    async def replace_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        self.state.jobs[booking_id] = list(jobs)

    async def add_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        self.state.jobs.setdefault(booking_id, []).extend(jobs)

    async def delete_pending_jobs(self, booking_id: int, types: Collection[object]) -> None:
        jobs = self.state.jobs.get(booking_id, [])
        self.state.jobs[booking_id] = [j for j in jobs if j.type not in types]

    async def get_closures(self, group_id: int, now: datetime) -> list[Closure]:
        found = [
            c
            for c in self.state.closures.values()
            if c.group_id == group_id and (c.ends_at is None or c.ends_at > now)
        ]
        return sorted(found, key=lambda c: c.starts_at)

    async def get_closure(self, closure_id: int) -> Closure | None:
        return self.state.closures.get(closure_id)

    async def add_closure(self, closure: Closure) -> Closure:
        stored = replace(closure, id=self._new_id())
        self.state.closures[stored.id or 0] = stored
        return stored

    async def save_closure(self, closure: Closure) -> None:
        self.state.closures[closure.id or 0] = closure

    async def find_confirmed_bookings(
        self, group_id: int, lower: datetime, upper: datetime | None
    ) -> list[Booking]:
        found = [
            b
            for b in self.state.bookings.values()
            if b.group_id == group_id
            and b.status is BookingStatus.CONFIRMED
            and b.kind == "scheduled"
            and lower <= b.start
            and (upper is None or b.start < upper)
        ]
        return sorted(found, key=lambda b: (b.start, b.id or 0))

    async def list_suspended_bookings(self, scope: Scope, group_id: int) -> list[Booking]:
        if not self._in_scope(scope, group_id):
            return []
        found = [
            b
            for b in self.state.bookings.values()
            if b.group_id == group_id and b.status is BookingStatus.SUSPENDED
        ]
        return sorted(found, key=lambda b: (b.start, b.id or 0))

    async def get_api_settings(self) -> ApiSettings:
        return self.state.api_settings

    async def save_api_settings(self, settings: ApiSettings) -> None:
        self.state.api_settings = settings

    async def list_api_clients(self, scope: Scope) -> list[ApiClient]:
        found = [c for c in self.state.api_clients.values() if scope.contains(c.organisation, None)]
        return sorted(found, key=lambda c: c.name)

    async def get_api_client(self, client_id: str) -> ApiClient | None:
        return self.state.api_clients.get(client_id)

    async def add_api_client(self, client: ApiClient) -> ApiClient:
        stored = replace(client, id=self._new_id())
        self.state.api_clients[client.client_id] = stored
        return stored

    async def save_api_client(self, client: ApiClient) -> None:
        self.state.api_clients[client.client_id] = client

    async def add_access_token(self, token: AccessToken) -> None:
        self.state.access_tokens[token.token_hash] = token

    async def get_access_token(self, token_hash: str) -> AccessToken | None:
        return self.state.access_tokens.get(token_hash)

    async def revoke_access_tokens(self, client_id: str) -> int:
        doomed = [h for h, t in self.state.access_tokens.items() if t.client_id == client_id]
        for token_hash in doomed:
            del self.state.access_tokens[token_hash]
        return len(doomed)

    async def prune_access_tokens(self, client_id: str, now: datetime, keep: int) -> None:
        tokens = self.state.access_tokens
        for token_hash in [h for h, t in tokens.items() if t.expires_at <= now]:
            del tokens[token_hash]
        own = sorted(
            (t for t in tokens.values() if t.client_id == client_id), key=lambda t: t.issued_at
        )
        for token in own[: max(len(own) - keep, 0)]:
            del tokens[token.token_hash]

    async def get_release_check(self) -> ReleaseCheck | None:
        return self.state.release_check

    async def save_release_check(self, check: ReleaseCheck) -> None:
        self.state.release_check = check

    async def get_update_requests(self, limit: int = 20) -> list[UpdateRequest]:
        return sorted(self.state.update_requests.values(), key=lambda r: -(r.id or 0))[:limit]

    async def add_update_request(self, request: UpdateRequest) -> UpdateRequest:
        stored = replace(request, id=self.state.next_id)
        self.state.next_id += 1
        self.state.update_requests[stored.id or 0] = stored
        return stored

    async def save_update_request(self, request: UpdateRequest) -> None:
        self.state.update_requests[request.id or 0] = request


class InMemoryUnitOfWork:
    """Locked transactions are serialised; changes are kept only if the block succeeds."""

    def __init__(self, state: State | None = None) -> None:
        self.state = state or State()
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def read(self) -> AsyncIterator[InMemoryTransaction]:
        yield InMemoryTransaction(copy.deepcopy(self.state))

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[InMemoryTransaction]:
        async with self._lock:
            working = copy.deepcopy(self.state)
            yield InMemoryTransaction(working)
            await asyncio.sleep(0)  # let a competing transaction queue up on the lock
            self.state = working
