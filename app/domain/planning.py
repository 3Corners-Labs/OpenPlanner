"""Planning use cases for admins: groups, opening hours, exceptions and capacity (WP7b).

Every operation asks ``AdminService.authorize`` first (the matrix, ADR 0014), so the route's
own check is not the only line of defence. Each change runs in one locked transaction that
validates the whole schedule, writes, and records an audit event with what changed (field
names or ``rule.add``-style keys, never values).

    create_group     group.create     organisation level (the chosen organisation)
    update_settings  group.edit       group level
    add/change/split/remove rule, add/remove override, schedule, preview
                     schedule.manage / group.view   group level

Existing bookings are never touched: their capacity is snapshotted on the slot. The time grid
(time zone and slot length) is frozen while confirmed bookings lie ahead, because old and new
slots would overlap and capacity could no longer be counted per slot.
"""

from dataclasses import dataclass, fields, replace
from datetime import date, time, timedelta

from app.domain.admin import AdminService, Authorization, record_change, require
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.availability import SlotTime, slots_on
from app.domain.errors import DomainError, ErrorCode
from app.domain.holiday_service import effective_schedule
from app.domain.lookups import group_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import (
    RESERVED_SLUGS,
    Group,
    Organisation,
    PhoneMode,
    ScheduleOverride,
    ScheduleRule,
    TimeWindow,
    validate_schedule,
    windows_on,
)

TIME_GRID_FIELDS = ("slot_minutes", "timezone")
"""Settings that move existing slots; frozen while confirmed bookings lie ahead."""

MAX_PREVIEW_DAYS = 31


@dataclass(frozen=True)
class NewGroup:
    """What an admin enters to create a group; the rest starts at the defaults."""

    organisation_slug: str
    name: str
    slug: str
    timezone: str
    slot_minutes: int


@dataclass(frozen=True)
class GroupSettings:
    """The editable settings of a group (its slug and organisation never change)."""

    name: str
    timezone: str
    slot_minutes: int
    min_notice_minutes: int
    horizon_days: int
    phone_mode: PhoneMode
    dispatch_lead_minutes: int
    video_host_email: str | None
    wxcc_business_address: str | None
    wxcc_channel: str | None
    wxcc_routing_value: str | None
    direct_call_enabled: bool
    not_picked_up_minutes: int
    direct_call_max_concurrent: int | None


def settings_of(group: Group) -> GroupSettings:
    """Return the editable settings of ``group``."""
    return GroupSettings(**{f.name: getattr(group, f.name) for f in fields(GroupSettings)})


@dataclass(frozen=True)
class Schedule:
    """A group's weekly rules and its exceptions from today on, each with its id."""

    rules: list[ScheduleRule]
    overrides: list[ScheduleOverride]


@dataclass(frozen=True)
class PreviewDay:
    """The slots citizens are offered on one day (before bookings and notice)."""

    day: date
    slots: list[SlotTime]


class PlanningService:
    """Application service for editing groups and their planning.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises every operation and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    # --- groups ---------------------------------------------------------------------------

    async def organisations_for_new_group(self, principal: Principal) -> list[Organisation]:
        """Return the organisations ``principal`` may create a group in."""
        async with self._uow.read() as tx:
            return await tx.list_organisations(scope_for(principal, Permission.GROUP_CREATE))

    async def create_group(self, principal: Principal, new: NewGroup) -> Group:
        """Create a group in ``new.organisation_slug`` with default settings.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``, ``slug_taken`` or
                ``invalid_configuration`` with the field.
        """
        permission = Permission.GROUP_CREATE
        authorization = await self._admin.authorize(
            principal, permission, organisation_slug=new.organisation_slug
        )
        require(authorization)
        async with self._uow.locked() as tx:
            organisation = await tx.get_organisation(new.organisation_slug)
            if organisation is None:
                raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
            group = Group(
                id=0,
                organisation_id=organisation.id,
                name=new.name,
                slug=new.slug,
                timezone=new.timezone,
                slot_minutes=new.slot_minutes,
            )
            if group.slug in RESERVED_SLUGS or await tx.get_group(group.slug) is not None:
                raise DomainError(ErrorCode.SLUG_TAKEN, "slug")
            stored = await tx.add_group(group)
            await self._audit(tx, principal, permission, organisation.slug, stored.slug)
        return stored

    async def update_settings(
        self, principal: Principal, slug: str, settings: GroupSettings
    ) -> list[str]:
        """Apply ``settings`` to the group; return the names of the changed fields.

        Raises:
            DomainError: ``not_permitted``, ``invalid_configuration`` or
                ``group_has_future_bookings`` with the field.
        """
        permission = Permission.GROUP_EDIT
        authorization = require(await self._admin.authorize(principal, permission, group_slug=slug))
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            updated = replace(
                group, **{f.name: getattr(settings, f.name) for f in fields(settings)}
            )
            changed = sorted(
                f.name
                for f in fields(settings)
                if getattr(group, f.name) != getattr(updated, f.name)
            )
            if not changed:
                return []
            frozen = [name for name in TIME_GRID_FIELDS if name in changed]
            if frozen and await tx.count_future_bookings(group.id, self._clock.now()):
                raise DomainError(ErrorCode.GROUP_HAS_FUTURE_BOOKINGS, frozen[0])
            await tx.save_group(updated)
            await self._audit(
                tx, principal, permission, _organisation(authorization), slug, ",".join(changed)
            )
        return changed

    # --- opening hours and exceptions --------------------------------------------------------

    async def schedule(self, principal: Principal, slug: str) -> Schedule:
        """Return the group's rules and its exceptions from today (group time) on.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        require(await self._admin.authorize(principal, Permission.GROUP_VIEW, group_slug=slug))
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            rules, overrides = await tx.get_schedule_items(group.id, group.today(self._clock.now()))
        return Schedule(
            sorted(rules, key=lambda r: (r.weekday, r.window, r.valid_from or date.min)),
            sorted(overrides, key=lambda o: (o.day, o.window is not None, o.window)),
        )

    async def preview(self, principal: Principal, slug: str, days: int = 14) -> list[PreviewDay]:
        """Return the slots the schedule offers from today on, for ``days`` days.

        Only days with slots are returned. Holidays count (ADR 0025); bookings, notice and
        horizon are left out: this shows what the opening hours produce, not what is free.
        """
        require(await self._admin.authorize(principal, Permission.GROUP_VIEW, group_slug=slug))
        days = max(1, min(days, MAX_PREVIEW_DAYS))
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            today = group.today(self._clock.now())
            last = today + timedelta(days=days - 1)
            rules, overrides = await effective_schedule(tx, group, today, last)
        preview = []
        for offset in range(days):
            day = today + timedelta(days=offset)
            slots = slots_on(group, day, windows_on(day, rules, overrides))
            if slots:
                preview.append(PreviewDay(day, slots))
        return preview

    async def add_rule(self, principal: Principal, slug: str, rule: ScheduleRule) -> int:
        """Add a weekly opening window; it may not overlap another one.

        Raises:
            DomainError: ``not_permitted`` or ``invalid_configuration`` (``schedule_rules``).
        """
        authorization = await self._manage(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            rules, overrides = await tx.get_schedule_items(group.id)
            validate_schedule([*rules, rule], overrides)
            rule_id = await tx.add_schedule_rule(group.id, rule)
            await self._schedule_audit(tx, principal, authorization, slug, "rule.add")
        return rule_id

    async def rule(self, principal: Principal, slug: str, rule_id: int) -> ScheduleRule:
        """Return the group's weekly opening window ``rule_id``, to change it.

        Raises:
            DomainError: ``not_permitted`` or ``schedule_item_not_found``.
        """
        await self._manage(principal, slug)
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            rules, _ = await tx.get_schedule_items(group.id)
        return _rule_by_id(rules, rule_id)

    async def update_rule(
        self, principal: Principal, slug: str, rule_id: int, rule: ScheduleRule
    ) -> bool:
        """Replace weekly opening window ``rule_id``; ``False`` if nothing changed.

        The new window is validated against the other windows only, so it may overlap its own
        old times. Existing bookings keep their slots (capacity is snapshotted).

        Raises:
            DomainError: ``not_permitted``, ``schedule_item_not_found`` or
                ``invalid_configuration`` (``schedule_rules``).
        """
        authorization = await self._manage(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            rules, overrides = await tx.get_schedule_items(group.id)
            if _rule_by_id(rules, rule_id) == rule:
                return False
            others = [r for r in rules if r.id != rule_id]
            validate_schedule([*others, rule], overrides)
            await tx.update_schedule_rule(group.id, rule_id, rule)
            await self._schedule_audit(tx, principal, authorization, slug, "rule.edit")
        return True

    async def split_rule(
        self,
        principal: Principal,
        slug: str,
        rule_id: int,
        at: time,
        capacity: int,
        label: str | None = None,
    ) -> int:
        """Split weekly block ``rule_id`` at ``at`` into two touching blocks (ADR 0027).

        The first part keeps the block's id, agent count and label; the second part runs from
        ``at`` to the old end with ``capacity`` agents and ``label``. Both keep the weekday and
        validity period. One locked transaction replaces the one rule with the two and
        validates the whole schedule, like a change. Returns the id of the second part.

        Raises:
            DomainError: ``not_permitted``, ``schedule_item_not_found`` or
                ``invalid_configuration`` (``split_at``, ``capacity``, ``label`` or
                ``schedule_rules``).
        """
        authorization = await self._manage(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            rules, overrides = await tx.get_schedule_items(group.id)
            old = _rule_by_id(rules, rule_id)
            if not old.window.opens < at < old.window.closes:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "split_at")
            first = replace(old, window=replace(old.window, closes=at), id=None)
            second = replace(
                old, window=TimeWindow(at, old.window.closes, capacity, label), id=None
            )
            others = [r for r in rules if r.id != rule_id]
            validate_schedule([*others, first, second], overrides)
            await tx.update_schedule_rule(group.id, rule_id, first)
            second_id = await tx.add_schedule_rule(group.id, second)
            await self._schedule_audit(tx, principal, authorization, slug, "rule.split")
        return second_id

    async def remove_rule(self, principal: Principal, slug: str, rule_id: int) -> None:
        """Remove a weekly opening window of the group.

        Raises:
            DomainError: ``not_permitted`` or ``schedule_item_not_found``.
        """
        authorization = await self._manage(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            if not await tx.delete_schedule_rule(group.id, rule_id):
                raise DomainError(ErrorCode.SCHEDULE_ITEM_NOT_FOUND)
            await self._schedule_audit(tx, principal, authorization, slug, "rule.remove")

    async def add_override(
        self, principal: Principal, slug: str, override: ScheduleOverride
    ) -> int:
        """Close a date or give it its own hours; the date may not lie in the past.

        Raises:
            DomainError: ``not_permitted`` or ``invalid_configuration`` (``day`` or
                ``schedule_overrides``).
        """
        authorization = await self._manage(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            if override.day < group.today(self._clock.now()):
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "day")
            rules, overrides = await tx.get_schedule_items(group.id)
            validate_schedule(rules, [*overrides, override])
            override_id = await tx.add_schedule_override(group.id, override)
            await self._schedule_audit(tx, principal, authorization, slug, "override.add")
        return override_id

    async def remove_override(self, principal: Principal, slug: str, override_id: int) -> None:
        """Remove an exception of the group.

        Raises:
            DomainError: ``not_permitted`` or ``schedule_item_not_found``.
        """
        authorization = await self._manage(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            if not await tx.delete_schedule_override(group.id, override_id):
                raise DomainError(ErrorCode.SCHEDULE_ITEM_NOT_FOUND)
            await self._schedule_audit(tx, principal, authorization, slug, "override.remove")

    # --- helpers ---------------------------------------------------------------------------

    async def _manage(self, principal: Principal, slug: str) -> Authorization:
        """Authorise ``schedule.manage`` on the group."""
        return require(
            await self._admin.authorize(principal, Permission.SCHEDULE_MANAGE, group_slug=slug)
        )

    async def _schedule_audit(
        self,
        tx: Transaction,
        principal: Principal,
        authorization: Authorization,
        slug: str,
        detail: str,
    ) -> None:
        """Audit a schedule change."""
        await self._audit(
            tx, principal, Permission.SCHEDULE_MANAGE, _organisation(authorization), slug, detail
        )

    async def _audit(
        self,
        tx: Transaction,
        principal: Principal,
        permission: Permission,
        organisation: str | None,
        group: str,
        detail: str = "",
    ) -> None:
        """Record a successful change in the same transaction as the change itself."""
        await record_change(
            tx, self._clock.now(), principal, permission, organisation, group, detail
        )


def _rule_by_id(rules: list[ScheduleRule], rule_id: int) -> ScheduleRule:
    """Return the rule with ``rule_id`` from the group's own rules.

    Raises:
        DomainError: ``schedule_item_not_found`` (also for another group's rule).
    """
    for rule in rules:
        if rule.id == rule_id:
            return rule
    raise DomainError(ErrorCode.SCHEDULE_ITEM_NOT_FOUND)


def _organisation(authorization: Authorization) -> str | None:
    """Return the organisation slug of an authorised group-level target."""
    return authorization.target.organisation
