"""In-memory UnitOfWork with commit/rollback semantics, for domain service tests."""

import asyncio
import copy
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from app.domain.audit import AdminProfile, AuditEntry, AuditEvent
from app.domain.authorization import Scope
from app.domain.booking import Booking, BookingStatus, MeetingRef, Slot, SlotUsage
from app.domain.branding import Branding, Logo
from app.domain.calls import BookingEvent, BookingOverview, CallStatus, new_status
from app.domain.channels import ReminderRule
from app.domain.closure import Closure
from app.domain.job_plan import JobType, PlannedJob
from app.domain.scheduling import (
    Group,
    GroupSummary,
    Organisation,
    ScheduleOverride,
    ScheduleRule,
)


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
    closures: dict[int, Closure] = field(default_factory=dict)
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

    async def list_audit_events(self, scope: Scope, limit: int = 100) -> list[AuditEntry]:
        events = [e for e in self.state.audit if scope.contains(e.organisation, e.group)]
        profiles = self.state.admin_profiles
        return [
            AuditEntry(e, profiles[e.actor].display_name if e.actor in profiles else None)
            for e in reversed(events)
        ][:limit]

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

    async def get_call_status(self, booking_id: int) -> CallStatus | None:
        return self.state.call_status.get(booking_id)

    async def save_call_status(self, status: CallStatus) -> None:
        self.state.call_status[status.booking_id] = status

    async def get_reminder_rules(self, group_id: int) -> list[ReminderRule]:
        return list(self.state.reminder_rules.get(group_id, []))

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

    async def get_booking_by_token_hash(self, token_hash: str) -> Booking | None:
        return next((b for b in self.state.bookings.values() if b.token_hash == token_hash), None)

    async def save_booking(self, booking: Booking) -> None:
        self.state.bookings[booking.id or 0] = booking

    async def replace_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        self.state.jobs[booking_id] = list(jobs)

    async def add_jobs(self, booking_id: int, jobs: Sequence[PlannedJob]) -> None:
        self.state.jobs.setdefault(booking_id, []).extend(jobs)

    async def delete_pending_jobs(self, booking_id: int, types: Collection[JobType]) -> None:
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
