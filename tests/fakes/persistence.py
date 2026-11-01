"""In-memory UnitOfWork with commit/rollback semantics, for domain service tests."""

import asyncio
import copy
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from app.domain import holidays as holiday_model
from app.domain.agenda import ShortfallAcceptance
from app.domain.agent_term import TERM_KEYS
from app.domain.api_clients import AccessToken, ApiClient, ApiSettings
from app.domain.audit import AuditEntry, AuditEvent, AuditFilter, Category
from app.domain.authorization import CustomRole, RoleCatalogue, Scope
from app.domain.booking import (
    Booking,
    BookingKind,
    BookingStatus,
    Claim,
    MeetingRef,
    Slot,
    SlotUsage,
    TrialCall,
)
from app.domain.branding import Branding, FontFile, FontRole, Logo
from app.domain.calls import BookingEvent, BookingOverview, CallStatus, new_status
from app.domain.channel_settings import ChannelConfig, ChannelSettings
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.checks import GroupCheck
from app.domain.closure import Closure
from app.domain.contacts import ContactBlock
from app.domain.diagnostic_mode import DiagnosticMode
from app.domain.direct_queue import DirectCall
from app.domain.embedding import AllowedOrigins
from app.domain.entitlements import Directory, GroupRef, OrganisationRef
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import Answers, FormDefinition, raise_to_pii
from app.domain.identity_providers import (
    KEY_OVERLAP,
    GeneratedKey,
    OrganisationProvider,
    SigningKey,
)
from app.domain.images import ImageFile, ImageText
from app.domain.job_plan import JobCounts, JobStatus, JobSummary, PlannedJob
from app.domain.log_destinations import DestinationStatus, LogDestination
from app.domain.maintenance import OFF as MAINTENANCE_OFF
from app.domain.maintenance import MaintenanceState
from app.domain.own_certificate import OwnCertificate
from app.domain.retention import GroupRetentionCounts, PurgeCandidate, RetentionFacts
from app.domain.scenarios import ClosureScenario
from app.domain.scheduling import (
    Group,
    GroupSummary,
    Organisation,
    ScheduleOverride,
    ScheduleRule,
)
from app.domain.staff import (
    GrantHolders,
    Membership,
    RefusedEntitlement,
    StaffFilter,
    StaffMember,
    visible,
)
from app.domain.tasks import ActiveJob, ActiveJobs
from app.domain.tls import CertificateCheck
from app.domain.updates import ListedRelease, ReleaseCheck, RunningVersion, UpdateRequest
from app.domain.worker_check import WorkerHeartbeat
from tests.support.domain import NOW


@dataclass
class State:
    groups: dict[int, Group] = field(default_factory=dict)
    rules: dict[int, list[ScheduleRule]] = field(default_factory=dict)
    overrides: dict[int, list[ScheduleOverride]] = field(default_factory=dict)
    reminder_rules: dict[int, list[ReminderRule]] = field(default_factory=dict)
    slots: dict[int, Slot] = field(default_factory=dict)
    bookings: dict[int, Booking] = field(default_factory=dict)
    jobs: dict[int, list[PlannedJob]] = field(default_factory=dict)
    active_jobs: list[ActiveJob] = field(default_factory=list)
    organisation_slugs: dict[int, str] = field(default_factory=dict)
    organisations: dict[int, Organisation] = field(default_factory=dict)
    booking_events: dict[int, list[BookingEvent]] = field(default_factory=dict)
    call_status: dict[int, CallStatus] = field(default_factory=dict)
    webhook_events: dict[tuple[str, str], datetime] = field(default_factory=dict)
    audit: list[AuditEvent] = field(default_factory=list)
    diagnostic_modes: list[DiagnosticMode] = field(default_factory=list)
    staff: dict[str, StaffMember] = field(default_factory=dict)
    logos: dict[int, Logo] = field(default_factory=dict)
    fonts: dict[tuple[int, FontRole], FontFile] = field(default_factory=dict)
    contact_blocks: dict[int, ContactBlock] = field(default_factory=dict)
    allowed_origins: dict[int, AllowedOrigins] = field(default_factory=dict)
    group_contact_blocks: dict[int, ContactBlock] = field(default_factory=dict)
    group_checks: dict[int, GroupCheck] = field(default_factory=dict)
    holiday_calendars: dict[int, holiday_model.HolidayCalendar] = field(default_factory=dict)
    holidays: dict[int, holiday_model.Holiday] = field(default_factory=dict)
    organisation_holidays: dict[int, frozenset[int]] = field(default_factory=dict)
    group_holidays: dict[int, holiday_model.GroupHolidays] = field(default_factory=dict)
    closures: dict[int, Closure] = field(default_factory=dict)
    acceptances: dict[tuple[int, datetime], ShortfallAcceptance] = field(default_factory=dict)
    closure_images: dict[int, ImageFile] = field(default_factory=dict)
    scenarios: dict[int, ClosureScenario] = field(default_factory=dict)
    scenario_images: dict[int, ImageFile] = field(default_factory=dict)
    api_settings: ApiSettings = field(default_factory=ApiSettings)
    api_clients: dict[str, ApiClient] = field(default_factory=dict)
    identity_providers: dict[str, OrganisationProvider] = field(default_factory=dict)
    provider_secrets: dict[str, str] = field(default_factory=dict)
    log_destinations: dict[int, LogDestination] = field(default_factory=dict)
    log_secrets: dict[int, str] = field(default_factory=dict)
    log_status: dict[tuple[int, str], DestinationStatus] = field(default_factory=dict)
    signing_keys: dict[str, list[SigningKey]] = field(default_factory=dict)
    private_keys: dict[str, str] = field(default_factory=dict)
    access_tokens: dict[str, AccessToken] = field(default_factory=dict)
    channel_settings: dict[tuple[int, ChannelKind], ChannelSettings] = field(default_factory=dict)
    channel_secrets: dict[tuple[int, ChannelKind], str] = field(default_factory=dict)
    group_texts: dict[int, dict[tuple[str, str], str]] = field(default_factory=dict)
    organisation_texts: dict[int, dict[tuple[str, str], str]] = field(default_factory=dict)
    release_check: ReleaseCheck | None = None
    running_versions: list[RunningVersion] = field(default_factory=list)
    release_listing: list[ListedRelease] = field(default_factory=list)
    certificate_check: CertificateCheck | None = None
    own_certificate: OwnCertificate | None = None
    maintenance: MaintenanceState = MAINTENANCE_OFF
    update_requests: dict[int, UpdateRequest] = field(default_factory=dict)
    custom_roles: dict[int, CustomRole] = field(default_factory=dict)
    settings_version: int = 0
    platform_settings: dict[str, object] = field(default_factory=dict)
    platform_expiries: dict[str, datetime] = field(default_factory=dict)
    organisation_settings: dict[int, dict[str, object]] = field(default_factory=dict)
    platform_secrets: dict[str, str] = field(default_factory=dict)
    secrets_available: bool = True
    purged_at: dict[int, datetime] = field(default_factory=dict)
    forms: dict[int, FormDefinition] = field(default_factory=dict)
    failed_purges: set[int] = field(default_factory=set)
    failed_jobs: dict[int, list[JobSummary]] = field(default_factory=dict)
    worker_heartbeat: WorkerHeartbeat | None = None
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

    async def list_groups(self, scope: Scope, *, archived: bool = False) -> list[GroupSummary]:
        summaries = [
            self._summary(g) for g in self.state.groups.values() if g.is_archived == archived
        ]
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
        staff = self.state.staff
        return [
            AuditEntry(e, staff[e.actor].display_name if e.actor in staff else None)
            for e in reversed(events)
        ][:limit]

    async def delete_audit_events(
        self, category: Category, before: datetime, action: str | None = None
    ) -> int:
        kept = [
            e
            for e in self.state.audit
            if e.event_category is not category
            or e.at >= before
            or (action is not None and e.action != action)
        ]
        removed = len(self.state.audit) - len(kept)
        self.state.audit = kept
        return removed

    async def active_diagnostic_modes(self, now: datetime) -> list[DiagnosticMode]:
        return [m for m in self.state.diagnostic_modes if m.until > now]

    async def save_diagnostic_mode(self, mode: DiagnosticMode) -> None:
        self.state.diagnostic_modes = [
            m
            for m in self.state.diagnostic_modes
            if (m.organisation, m.group) != (mode.organisation, mode.group)
        ] + [mode]

    async def end_diagnostic_mode(
        self, organisation: str, group: str | None, now: datetime
    ) -> bool:
        active = [
            m
            for m in self.state.diagnostic_modes
            if (m.organisation, m.group) == (organisation, group) and m.until > now
        ]
        self.state.diagnostic_modes = [m for m in self.state.diagnostic_modes if m not in active]
        return bool(active)

    async def end_all_diagnostic_modes(self, now: datetime) -> int:
        active = [m for m in self.state.diagnostic_modes if m.until > now]
        self.state.diagnostic_modes = []
        return len(active)

    async def get_staff(self, subject: str) -> StaffMember | None:
        return self.state.staff.get(subject)

    async def save_staff(self, member: StaffMember) -> None:
        old = self.state.staff.get(member.subject)
        member_id = old.id if old and old.id is not None else self._new_id()
        self.state.staff[member.subject] = replace(member, id=member_id, recorded=True)

    async def set_staff_seen(self, subject: str, at: datetime) -> None:
        if subject in self.state.staff:
            self.state.staff[subject] = replace(self.state.staff[subject], last_seen_at=at)

    async def set_staff_logout(self, subject: str, at: datetime) -> None:
        if subject in self.state.staff:
            self.state.staff[subject] = replace(self.state.staff[subject], last_logout_at=at)

    async def list_staff(self, scope: Scope, narrow: StaffFilter) -> list[StaffMember]:
        found = [m for m in self.state.staff.values() if visible(m, scope) and narrow.matches(m)]
        return sorted(found, key=lambda m: (m.display_name, m.subject))

    async def get_directory(self) -> Directory:
        catalogue = RoleCatalogue(tuple(await self.get_custom_roles()))
        organisations = [
            OrganisationRef(o.slug, o.idp_key) for o in self.state.organisations.values()
        ]
        groups = [
            GroupRef(g.slug, g.idp_key, self.state.organisation_slugs.get(g.organisation_id, "org"))
            for g in self.state.groups.values()
        ]
        return Directory.of(catalogue, organisations, groups)

    async def list_grant_holders(self, scope: Scope) -> list[GrantHolders]:
        found: dict[Membership, GrantHolders] = {}
        for member in self.state.staff.values():
            for m in member.memberships:
                if not (scope.everything or m.organisation in scope.organisations):
                    continue
                before = found.get(m)
                found[m] = GrantHolders(
                    m,
                    (before.holders if before else 0) + 1,
                    max(before.last_login_at, member.last_login_at)
                    if before
                    else member.last_login_at,
                )
        return sorted(found.values(), key=lambda h: h.membership.sort_key)

    async def list_refused_entitlements(
        self, scope: Scope, since: datetime
    ) -> list[RefusedEntitlement]:
        return sorted(
            (
                r
                for member in self.state.staff.values()
                for r in member.refused
                if r.received_at >= since
                and (scope.everything or r.organisation in scope.organisations)
            ),
            key=lambda r: (r.received_at, r.value),
            reverse=True,
        )

    async def get_group(self, slug: str) -> Group | None:
        return next((g for g in self.state.groups.values() if g.slug == slug), None)

    async def get_group_by_id(self, group_id: int) -> Group | None:
        return self.state.groups.get(group_id)

    async def get_form(self, group_id: int) -> FormDefinition:
        return self.state.forms.get(group_id, FormDefinition(1))

    async def save_form(self, group_id: int, form: FormDefinition) -> None:
        self.state.forms[group_id] = form

    async def raise_answers_to_pii(self, group_id: int, key: str) -> int:
        changed = 0
        for booking_id, booking in self.state.bookings.items():
            contact = booking.contact
            if booking.group_id != group_id or contact is None or contact.answers is None:
                continue
            stored = raise_to_pii(contact.answers.to_storage(), key)
            answers = Answers.from_storage(stored)
            self.state.bookings[booking_id] = replace(
                booking, contact=replace(contact, answers=answers)
            )
            changed += 1
        return changed

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

    async def save_worker_heartbeat(self, heartbeat: WorkerHeartbeat) -> None:
        self.state.worker_heartbeat = heartbeat

    async def get_worker_heartbeat(self) -> WorkerHeartbeat | None:
        return self.state.worker_heartbeat

    async def get_organisation_by_id(self, organisation_id: int) -> Organisation | None:
        return self.state.organisations.get(organisation_id)

    async def add_organisation(self, organisation: Organisation) -> Organisation:
        stored = replace(organisation, id=self._new_id())
        self.state.organisations[stored.id] = stored
        self.state.organisation_slugs[stored.id] = stored.slug
        return stored

    async def count_groups(self, organisation_id: int) -> int:
        return sum(1 for g in self.state.groups.values() if g.organisation_id == organisation_id)

    async def count_api_clients(self, organisation_id: int) -> int:
        slug = self.state.organisation_slugs.get(organisation_id)
        return sum(1 for c in self.state.api_clients.values() if c.organisation == slug)

    async def delete_organisation(self, organisation_id: int) -> None:
        del self.state.organisations[organisation_id]
        self.state.organisation_slugs.pop(organisation_id, None)
        for table in (self.state.contact_blocks, self.state.allowed_origins, self.state.logos):
            table.pop(organisation_id, None)

    async def save_organisation(self, organisation: Organisation) -> None:
        current = self.state.organisations[organisation.id]
        self.state.organisations[organisation.id] = replace(
            current,
            name=organisation.name,
            default_locale=organisation.default_locale,
            idp_key=organisation.idp_key,
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

    async def get_font(self, organisation_slug: str, role: FontRole) -> FontFile | None:
        organisation = await self.get_organisation(organisation_slug)
        return self.state.fonts.get((organisation.id, role)) if organisation else None

    async def save_font(self, organisation_id: int, role: FontRole, font: FontFile | None) -> None:
        organisation = self.state.organisations[organisation_id]
        if font is None:
            self.state.fonts.pop((organisation_id, role), None)
        else:
            self.state.fonts[(organisation_id, role)] = font
        fonts = tuple(f.ref for (org, _), f in self.state.fonts.items() if org == organisation_id)
        branding = replace(organisation.branding, fonts=fonts)
        self.state.organisations[organisation_id] = replace(organisation, branding=branding)

    async def get_contact_block(self, organisation_id: int) -> ContactBlock | None:
        return self.state.contact_blocks.get(organisation_id)

    async def save_contact_block(self, organisation_id: int, block: ContactBlock | None) -> None:
        if block is None:
            self.state.contact_blocks.pop(organisation_id, None)
        else:
            self.state.contact_blocks[organisation_id] = block

    async def get_allowed_origins(self, organisation_id: int) -> AllowedOrigins:
        return self.state.allowed_origins.get(organisation_id, AllowedOrigins())

    async def save_allowed_origins(self, organisation_id: int, allowed: AllowedOrigins) -> None:
        self.state.allowed_origins[organisation_id] = allowed

    async def get_group_contact_block(self, group_id: int) -> ContactBlock | None:
        return self.state.group_contact_blocks.get(group_id)

    async def save_group_contact_block(self, group_id: int, block: ContactBlock | None) -> None:
        if block is None:
            self.state.group_contact_blocks.pop(group_id, None)
        else:
            self.state.group_contact_blocks[group_id] = block

    async def add_group(self, group: Group) -> Group:
        stored = replace(group, id=self._new_id(), idp_key=group.slug)
        self.state.groups[stored.id] = stored
        return stored

    async def save_group(self, group: Group) -> None:
        self.state.groups[group.id] = group

    async def erasable_groups(self, archived_before: datetime) -> list[Group]:
        def referenced(group_id: int) -> bool:
            return (
                any(s.group_id == group_id for s in self.state.slots.values())
                or any(b.group_id == group_id for b in self.state.bookings.values())
                or any(c.group_id == group_id for c in self.state.closures.values())
                or any(a.group_id == group_id for a in self.state.acceptances.values())
                or any(
                    g.slug in c.groups
                    for c in self.state.api_clients.values()
                    for g in [self.state.groups[group_id]]
                )
            )

        return [
            g
            for g in sorted(self.state.groups.values(), key=lambda g: (g.archived_at or NOW, g.id))
            if g.archived_at is not None
            and g.archived_at <= archived_before
            and not referenced(g.id)
        ]

    async def delete_group(self, group_id: int) -> None:
        del self.state.groups[group_id]
        for table in (self.state.rules, self.state.overrides, self.state.reminder_rules):
            table.pop(group_id, None)

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

    async def update_schedule_override(
        self, group_id: int, override_id: int, override: ScheduleOverride
    ) -> bool:
        overrides = self.state.overrides.get(group_id, [])
        found = any(o.id == override_id for o in overrides)
        self.state.overrides[group_id] = [
            replace(override, id=override_id) if o.id == override_id else o for o in overrides
        ]
        return found

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

    async def save_holiday_calendar(self, calendar: holiday_model.HolidayCalendar) -> None:
        self.state.holiday_calendars[calendar.id] = calendar

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

    async def save_trial_call(self, booking_id: int, trial: TrialCall) -> None:
        booking = self.state.bookings[booking_id]
        self.state.bookings[booking_id] = replace(
            booking, trial=trial, trial_count=booking.trial_count + 1
        )

    async def clear_trial_call(self, booking_id: int) -> None:
        self.state.bookings[booking_id] = replace(self.state.bookings[booking_id], trial=None)

    async def save_task_id(self, booking_id: int, task_id: str) -> None:
        booking = self.state.bookings[booking_id]
        self.state.bookings[booking_id] = replace(booking, wxcc_task_id=task_id)

    async def get_booking_by_meeting(self, meeting_id: str) -> Booking | None:
        return next(
            (b for b in self.state.bookings.values() if b.meeting and b.meeting.id == meeting_id),
            None,
        )

    async def get_booking_by_trial_meeting(self, meeting_id: str) -> Booking | None:
        return next(
            (
                b
                for b in self.state.bookings.values()
                if b.trial and b.trial.meeting.id == meeting_id
            ),
            None,
        )

    async def claim_booking(self, booking_id: int, claim: Claim) -> bool:
        booking = self.state.bookings[booking_id]
        if booking.claim is not None or not booking.is_active:
            return False
        self.state.bookings[booking_id] = replace(booking, claim=claim)
        return True

    async def release_claim(self, booking_id: int) -> None:
        booking = self.state.bookings[booking_id]
        self.state.bookings[booking_id] = replace(booking, claim=None)

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
                claimed=b.claim is not None,
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

    async def update_reminder_rule(
        self, group_id: int, channel: ChannelKind, minutes_before: int, rule: ReminderRule
    ) -> bool:
        rules = self.state.reminder_rules.get(group_id, [])
        found = any((r.channel, r.minutes_before) == (channel, minutes_before) for r in rules)
        self.state.reminder_rules[group_id] = [
            rule if (r.channel, r.minutes_before) == (channel, minutes_before) else r for r in rules
        ]
        return found

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

    async def get_organisation_texts(self, organisation_id: int) -> dict[tuple[str, str], str]:
        return dict(self.state.organisation_texts.get(organisation_id, {}))

    async def save_organisation_text(
        self, organisation_id: int, locale: str, key: str, text: str | None
    ) -> None:
        texts = self.state.organisation_texts.setdefault(organisation_id, {})
        if text is None:
            texts.pop((locale, key), None)
        else:
            texts[(locale, key)] = text

    async def get_all_organisation_texts(self) -> dict[tuple[int, str, str], str]:
        return {
            (org, locale, key): text
            for org, texts in self.state.organisation_texts.items()
            for (locale, key), text in texts.items()
        }

    async def get_all_group_terms(self) -> dict[tuple[int, str, str], str]:
        return {
            (group, locale, key): text
            for group, texts in self.state.group_texts.items()
            for (locale, key), text in texts.items()
            if key in TERM_KEYS
        }

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

    async def upcoming_starts_for_email(
        self, group_id: int, email: str, after: datetime
    ) -> list[datetime]:
        return [
            b.start
            for b in self.state.bookings.values()
            if b.group_id == group_id
            and b.kind is BookingKind.SCHEDULED
            and b.holds_place
            and b.end > after
            and b.contact is not None
            and b.contact.email.lower() == email.lower()
        ]

    async def add_booking(self, booking: Booking) -> Booking:
        stored = replace(booking, id=self._new_id())
        self.state.bookings[stored.id or 0] = stored
        return stored

    async def get_booking(self, booking_id: int) -> Booking | None:
        return self.state.bookings.get(booking_id)

    async def purge_personal_data(self, booking_id: int, now: datetime) -> None:
        self.state.bookings[booking_id] = replace(
            self.state.bookings[booking_id], contact=None, claim=None, trial=None
        )
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

    async def active_jobs(self, limit: int) -> ActiveJobs:
        return ActiveJobs(list(self.state.active_jobs[:limit]), len(self.state.active_jobs))

    async def get_booking_jobs(self, booking_id: int) -> list[JobSummary]:
        # Planned jobs are pending; ``failed_jobs`` holds what a test says failed (ADR 0068).
        pending = [
            JobSummary(j.type, JobStatus.PENDING, j.run_at, 0, "", j.channel)
            for j in self.state.jobs.get(booking_id, [])
        ]
        return sorted(
            [*self.state.failed_jobs.get(booking_id, []), *pending], key=lambda j: j.run_at
        )

    async def count_group_jobs(self, group_id: int, overdue_before: datetime) -> JobCounts:
        bookings = {b.id for b in self.state.bookings.values() if b.group_id == group_id}
        failed = sum(len(self.state.failed_jobs.get(b, [])) for b in bookings)
        overdue = sum(
            1 for b in bookings for j in self.state.jobs.get(b, []) if j.run_at < overdue_before
        )
        return JobCounts(failed, overdue)

    async def get_shortfall_acceptances(
        self, group_id: int, lower: datetime, upper: datetime
    ) -> list[ShortfallAcceptance]:
        return [
            a
            for (gid, start), a in sorted(self.state.acceptances.items())
            if gid == group_id and lower <= start < upper
        ]

    async def save_shortfall_acceptance(self, acceptance: ShortfallAcceptance) -> None:
        self.state.acceptances[(acceptance.group_id, acceptance.start)] = acceptance

    async def count_audit_events(
        self, actor: str, actions: Collection[str], since: datetime
    ) -> int:
        return sum(
            1
            for e in self.state.audit
            if e.actor == actor and e.action in actions and e.at >= since
        )

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
        # As the SQL adapter: the image has its own calls and is kept.
        current = self.state.closures.get(closure.id or 0)
        image = current.image if current else None
        self.state.closures[closure.id or 0] = replace(closure, image=image)

    async def get_closure_image(self, closure_id: int) -> ImageFile | None:
        return self.state.closure_images.get(closure_id)

    async def save_closure_image(
        self, closure_id: int, text: ImageText, image: ImageFile | None
    ) -> None:
        stored = image or self.state.closure_images[closure_id]
        self.state.closure_images[closure_id] = stored
        closure = self.state.closures[closure_id]
        self.state.closures[closure_id] = replace(closure, image=stored.ref(text))

    async def list_scenarios(
        self, scope: Scope, organisation_id: int, group_id: int | None
    ) -> list[ClosureScenario]:
        organisation = self.state.organisation_slugs.get(organisation_id)
        groups = [g for g in self.state.groups.values() if g.organisation_id == organisation_id]

        def in_scope(scenario: ClosureScenario) -> bool:
            if scenario.group_id is not None:
                return scope.contains(organisation, self.state.groups[scenario.group_id].slug)
            return scope.contains(organisation, None) or any(
                scope.contains(organisation, g.slug) for g in groups
            )

        found = [
            s
            for s in self.state.scenarios.values()
            if s.organisation_id == organisation_id and s.group_id == group_id and in_scope(s)
        ]
        return sorted(found, key=lambda s: s.name)

    async def get_scenario(self, scenario_id: int) -> ClosureScenario | None:
        return self.state.scenarios.get(scenario_id)

    async def add_scenario(self, scenario: ClosureScenario) -> ClosureScenario:
        stored = replace(scenario, id=self._new_id(), image=None)
        self.state.scenarios[stored.id or 0] = stored
        return stored

    async def save_scenario(self, scenario: ClosureScenario) -> None:
        current = self.state.scenarios[scenario.id or 0]
        self.state.scenarios[scenario.id or 0] = replace(scenario, image=current.image)

    async def delete_scenario(self, scenario_id: int) -> None:
        self.state.scenarios.pop(scenario_id, None)
        self.state.scenario_images.pop(scenario_id, None)

    async def get_scenario_image(self, scenario_id: int) -> ImageFile | None:
        return self.state.scenario_images.get(scenario_id)

    async def save_scenario_image(
        self, scenario_id: int, text: ImageText, image: ImageFile | None
    ) -> None:
        stored = image or self.state.scenario_images[scenario_id]
        self.state.scenario_images[scenario_id] = stored
        scenario = self.state.scenarios[scenario_id]
        self.state.scenarios[scenario_id] = replace(scenario, image=stored.ref(text))

    async def remove_scenario_image(self, scenario_id: int) -> None:
        self.state.scenario_images.pop(scenario_id, None)
        scenario = self.state.scenarios[scenario_id]
        self.state.scenarios[scenario_id] = replace(scenario, image=None)

    async def remove_closure_image(self, closure_id: int) -> None:
        self.state.closure_images.pop(closure_id, None)
        closure = self.state.closures[closure_id]
        self.state.closures[closure_id] = replace(closure, image=None)

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

    async def list_suspended_bookings(
        self, scope: Scope, group_id: int, status: BookingStatus = BookingStatus.SUSPENDED
    ) -> list[Booking]:
        if not self._in_scope(scope, group_id):
            return []
        found = [
            b for b in self.state.bookings.values() if b.group_id == group_id and b.status is status
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

    async def delete_api_client(self, client_id: str) -> None:
        del self.state.api_clients[client_id]
        self.state.access_tokens = {
            h: t for h, t in self.state.access_tokens.items() if t.client_id != client_id
        }

    async def list_identity_providers(self, scope: Scope) -> list[OrganisationProvider]:
        found = [
            p
            for p in self.state.identity_providers.values()
            if scope.contains(p.organisation, None)
        ]
        return sorted(found, key=lambda p: (p.organisation, p.key))

    async def get_identity_provider(self, key: str) -> OrganisationProvider | None:
        return self.state.identity_providers.get(key)

    async def add_identity_provider(
        self, provider: OrganisationProvider, secret: str | None
    ) -> OrganisationProvider:
        if await self.get_organisation(provider.organisation) is None:
            raise LookupError("unknown organisation")
        stored = replace(provider, id=self._new_id())
        self.state.identity_providers[provider.key] = stored
        if secret is not None:
            self.state.provider_secrets[provider.key] = secret
        return stored

    async def save_identity_provider(
        self, provider: OrganisationProvider, secret: str | None
    ) -> None:
        self.state.identity_providers[provider.key] = provider
        if secret is not None:
            self.state.provider_secrets[provider.key] = secret

    async def delete_identity_provider(self, key: str) -> bool:
        self.state.provider_secrets.pop(key, None)
        for stored in self.state.signing_keys.pop(key, []):
            self.state.private_keys.pop(stored.kid, None)
        return self.state.identity_providers.pop(key, None) is not None

    async def list_log_destinations(self, scope: Scope) -> list[LogDestination]:
        if not scope.everything:
            return []
        return sorted(self.state.log_destinations.values(), key=lambda d: d.name)

    async def get_log_destination(self, destination_id: int) -> LogDestination | None:
        return self.state.log_destinations.get(destination_id)

    async def add_log_destination(
        self, destination: LogDestination, secret: str | None
    ) -> LogDestination:
        if any(d.name == destination.name for d in self.state.log_destinations.values()):
            raise DomainError(ErrorCode.LOG_DESTINATION_NAME_TAKEN, "name")
        stored = replace(destination, id=self._new_id())
        self.state.log_destinations[stored.id or 0] = stored
        if secret is not None:
            self.state.log_secrets[stored.id or 0] = secret
        return stored

    async def save_log_destination(self, destination: LogDestination, secret: str | None) -> None:
        if destination.id not in self.state.log_destinations:
            raise DomainError(ErrorCode.LOG_DESTINATION_NOT_FOUND)
        if any(
            d.name == destination.name and d.id != destination.id
            for d in self.state.log_destinations.values()
        ):
            raise DomainError(ErrorCode.LOG_DESTINATION_NAME_TAKEN, "name")
        self.state.log_destinations[destination.id or 0] = destination
        if secret is not None:
            self.state.log_secrets[destination.id or 0] = secret

    async def delete_log_destination(self, destination_id: int) -> bool:
        self.state.log_secrets.pop(destination_id, None)
        for key in [k for k in self.state.log_status if k[0] == destination_id]:
            del self.state.log_status[key]
        return self.state.log_destinations.pop(destination_id, None) is not None

    async def save_log_destination_status(self, status: DestinationStatus, at: datetime) -> None:
        if status.destination_id in self.state.log_destinations:
            self.state.log_status[(status.destination_id, status.service)] = replace(
                status, updated_at=at
            )

    async def list_log_destination_status(self, scope: Scope) -> list[DestinationStatus]:
        if not scope.everything:
            return []
        return [self.state.log_status[k] for k in sorted(self.state.log_status)]

    async def get_signing_keys(self, provider_key: str) -> list[SigningKey]:
        keys = self.state.signing_keys.get(provider_key, [])
        return sorted(keys, key=lambda k: (k.created_at, k.kid), reverse=True)

    async def add_signing_key(self, provider_key: str, key: GeneratedKey, now: datetime) -> None:
        self.state.signing_keys.setdefault(provider_key, []).append(
            SigningKey(provider_key, key.kid, dict(key.public_jwk), now)
        )
        self.state.private_keys[key.kid] = key.private_pem

    async def retire_signing_keys(self, provider_key: str, *, keep: str, at: datetime) -> None:
        self.state.signing_keys[provider_key] = [
            replace(k, retired_at=at) if k.kid != keep and k.retired_at is None else k
            for k in self.state.signing_keys.get(provider_key, [])
        ]

    async def published_signing_keys(self, now: datetime) -> list[SigningKey]:
        return [
            k
            for keys in self.state.signing_keys.values()
            for k in keys
            if k.retired_at is None or now - k.retired_at < KEY_OVERLAP
        ]

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

    async def get_release_listing(self) -> list[ListedRelease]:
        return sorted(self.state.release_listing, key=lambda item: item.version, reverse=True)

    async def save_release_listing(self, listing: Sequence[ListedRelease]) -> None:
        self.state.release_listing = list(listing)

    async def get_running_versions(self, limit: int = 2) -> list[RunningVersion]:
        return list(reversed(self.state.running_versions))[:limit]

    async def add_running_version(self, running: RunningVersion) -> RunningVersion:
        self.state.running_versions.append(running)
        return running

    async def get_certificate_check(self) -> CertificateCheck | None:
        return self.state.certificate_check

    async def save_certificate_check(self, check: CertificateCheck) -> None:
        self.state.certificate_check = check

    async def get_own_certificate(self) -> OwnCertificate | None:
        return self.state.own_certificate

    async def save_own_certificate(self, certificate: OwnCertificate) -> None:
        self.state.own_certificate = certificate

    async def delete_own_certificate(self) -> bool:
        had = self.state.own_certificate is not None
        self.state.own_certificate = None
        return had

    async def get_maintenance_state(self) -> MaintenanceState:
        return self.state.maintenance

    async def save_maintenance_state(self, state: MaintenanceState) -> None:
        self.state.maintenance = state

    async def get_update_requests(self, limit: int = 20) -> list[UpdateRequest]:
        return sorted(self.state.update_requests.values(), key=lambda r: -(r.id or 0))[:limit]

    async def add_update_request(self, request: UpdateRequest) -> UpdateRequest:
        stored = replace(request, id=self.state.next_id)
        self.state.next_id += 1
        self.state.update_requests[stored.id or 0] = stored
        return stored

    async def save_update_request(self, request: UpdateRequest) -> None:
        self.state.update_requests[request.id or 0] = request

    async def get_events(self, actions: Sequence[str], limit: int) -> list[AuditEntry]:
        staff = self.state.staff
        return [
            AuditEntry(e, staff[e.actor].display_name if e.actor in staff else None)
            for e in reversed(self.state.audit)
            if e.action in actions
        ][:limit]

    async def get_custom_roles(self) -> list[CustomRole]:
        return [self.state.custom_roles[k] for k in sorted(self.state.custom_roles)]

    async def get_custom_role(self, role_id: int) -> CustomRole | None:
        return self.state.custom_roles.get(role_id)

    async def add_custom_role(self, role: CustomRole) -> CustomRole:
        stored = replace(role, id=self._new_id())
        self.state.custom_roles[stored.id] = stored
        return stored

    async def save_custom_role(self, role: CustomRole) -> None:
        self.state.custom_roles[role.id] = role

    async def delete_custom_role(self, role_id: int) -> None:
        self.state.custom_roles.pop(role_id, None)

    async def settings_version(self) -> int:
        return self.state.settings_version

    async def platform_settings(self) -> dict[str, object]:
        return dict(self.state.platform_settings)

    async def platform_setting_expiries(self) -> dict[str, datetime]:
        return dict(self.state.platform_expiries)

    async def save_platform_setting(
        self, key: str, value: object, updated_at: datetime, expires_at: datetime | None
    ) -> None:
        self.state.platform_settings[key] = value
        if expires_at is None:
            self.state.platform_expiries.pop(key, None)
        else:
            self.state.platform_expiries[key] = expires_at

    async def delete_platform_setting(self, key: str) -> bool:
        self.state.platform_expiries.pop(key, None)
        present = key in self.state.platform_settings
        self.state.platform_settings.pop(key, None)
        return present

    async def bump_settings_version(self) -> int:
        self.state.settings_version += 1
        return self.state.settings_version

    async def platform_secrets(self) -> dict[str, str]:
        return dict(self.state.platform_secrets)

    async def secrets_available(self) -> bool:
        return self.state.secrets_available

    async def save_platform_secret(self, key: str, value: str, updated_at: datetime) -> None:
        self.state.platform_secrets[key] = value

    async def delete_platform_secret(self, key: str) -> bool:
        return self.state.platform_secrets.pop(key, None) is not None

    async def organisation_settings(self) -> dict[int, dict[str, object]]:
        return {k: dict(v) for k, v in self.state.organisation_settings.items() if v}

    async def save_organisation_setting(
        self, organisation_id: int, key: str, value: object, updated_at: datetime
    ) -> None:
        self.state.organisation_settings.setdefault(organisation_id, {})[key] = value

    async def delete_organisation_setting(self, organisation_id: int, key: str) -> bool:
        values = self.state.organisation_settings.get(organisation_id, {})
        present = key in values
        values.pop(key, None)
        return present


class InMemoryUnitOfWork:
    """Locked transactions are serialised; changes are kept only if the block succeeds."""

    def __init__(self, state: State | None = None) -> None:
        self.state = state or State()
        self._lock = asyncio.Lock()
        self.reads = 0

    @asynccontextmanager
    async def read(self) -> AsyncIterator[InMemoryTransaction]:
        self.reads += 1
        yield InMemoryTransaction(copy.deepcopy(self.state))

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[InMemoryTransaction]:
        async with self._lock:
            working = copy.deepcopy(self.state)
            yield InMemoryTransaction(working)
            await asyncio.sleep(0)  # let a competing transaction queue up on the lock
            self.state = working
