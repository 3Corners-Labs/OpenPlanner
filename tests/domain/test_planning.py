from dataclasses import replace
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Principal
from app.domain.booking import Booking, BookingStatus, Slot
from app.domain.errors import DomainError
from app.domain.planning import NewGroup, PlanningService, settings_of
from app.domain.scheduling import PhoneMode, ScheduleOverride, ScheduleRule, TimeWindow
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import (
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SUPER,
    audit_rows,
    two_organisations,
)
from tests.support.domain import NOW, SLOT_0900, window

TODAY = date(2026, 1, 1)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


@pytest.fixture
def planning(uow: InMemoryUnitOfWork) -> PlanningService:
    clock = FixedClock(NOW)
    return PlanningService(uow, clock, AdminService(uow, clock))


def _rule(weekday: int = 0, opens: str = "09:00", closes: str = "12:00", capacity: int = 2):
    return ScheduleRule(weekday, window(opens, closes, capacity))


# --- creating groups ---------------------------------------------------------------------


async def test_org_admin_creates_a_group_in_their_organisation(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    group = await planning.create_group(
        ORG_ADMIN, NewGroup("demo", "Vergunningen", "vergunningen", "Europe/Amsterdam", 20)
    )

    assert group.id not in (0, None)
    assert (group.organisation_id, group.slug, group.slot_minutes) == (1, "vergunningen", 20)
    assert audit_rows(uow) == [
        ("o-1", "group.create", Outcome.SUCCESS, "", "vergunningen"),
    ]
    assert uow.state.audit[0].organisation == "demo"


async def test_organisations_offered_for_a_new_group_follow_the_matrix(
    planning: PlanningService,
) -> None:
    assert [o.slug for o in await planning.organisations_for_new_group(ORG_ADMIN)] == ["demo"]
    assert [o.slug for o in await planning.organisations_for_new_group(SUPER)] == [
        "andere",
        "demo",
    ]
    assert await planning.organisations_for_new_group(GROUP_ADMIN) == []


@pytest.mark.parametrize("principal", [OUTSIDER, GROUP_ADMIN])
async def test_creating_outside_the_scope_is_refused_and_audited(
    planning: PlanningService, uow: InMemoryUnitOfWork, principal: Principal
) -> None:
    with pytest.raises(DomainError, match="not_permitted"):
        await planning.create_group(
            principal, NewGroup("demo", "X", "x-team", "Europe/Amsterdam", 30)
        )

    assert [(e.action, e.outcome) for e in uow.state.audit] == [("group.create", Outcome.DENIED)]
    assert "x-team" not in {g.slug for g in uow.state.groups.values()}


async def test_slug_must_be_unique_and_organisation_must_exist(
    planning: PlanningService,
) -> None:
    with pytest.raises(DomainError, match="slug_taken"):
        await planning.create_group(
            SUPER, NewGroup("demo", "Dubbel", "andere-team", "Europe/Amsterdam", 30)
        )
    with pytest.raises(DomainError, match="organisation_not_found"):
        await planning.create_group(SUPER, NewGroup("nope", "X", "x", "Europe/Amsterdam", 30))


async def test_reserved_slugs_cannot_be_taken(planning: PlanningService) -> None:
    with pytest.raises(DomainError, match="slug_taken"):
        await planning.create_group(SUPER, NewGroup("demo", "Nieuw", "new", "Europe/Amsterdam", 30))


async def test_invalid_new_group_names_the_field(planning: PlanningService) -> None:
    with pytest.raises(DomainError, match="invalid_configuration: slug"):
        await planning.create_group(
            SUPER, NewGroup("demo", "X", "Niet Geldig", "Europe/Amsterdam", 30)
        )


# --- group settings ----------------------------------------------------------------------


async def test_settings_change_records_the_changed_fields(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    current = settings_of(uow.state.groups[1])
    changed = await planning.update_settings(
        ORG_ADMIN,
        "demo",
        replace(current, name="Demo-team", phone_mode=PhoneMode.REQUIRED, horizon_days=30),
    )

    assert changed == ["horizon_days", "name", "phone_mode"]
    group = uow.state.groups[1]
    assert (group.name, group.phone_mode, group.horizon_days) == (
        "Demo-team",
        PhoneMode.REQUIRED,
        30,
    )
    assert audit_rows(uow) == [
        ("o-1", "group.edit", Outcome.SUCCESS, "horizon_days,name,phone_mode", "demo")
    ]


async def test_unchanged_settings_write_nothing(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    current = settings_of(uow.state.groups[1])

    assert await planning.update_settings(ORG_ADMIN, "demo", current) == []
    assert uow.state.audit == []


async def test_group_admin_may_not_change_group_settings(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    current = settings_of(uow.state.groups[1])

    with pytest.raises(DomainError, match="not_permitted"):
        await planning.update_settings(GROUP_ADMIN, "demo", replace(current, name="Mijn naam"))
    assert uow.state.groups[1].name == "Demo"
    assert [(e.action, e.outcome) for e in uow.state.audit] == [("group.edit", Outcome.DENIED)]


async def test_invalid_settings_name_the_field_and_change_nothing(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    current = settings_of(uow.state.groups[1])

    with pytest.raises(DomainError, match="invalid_configuration: slot_minutes"):
        await planning.update_settings(SUPER, "demo", replace(current, slot_minutes=7))
    assert uow.state.groups[1].slot_minutes == 30


def _future_booking(uow: InMemoryUnitOfWork, status: BookingStatus) -> None:
    end = SLOT_0900 + timedelta(minutes=30)
    uow.state.slots[50] = Slot(50, 1, SLOT_0900, end, 2)
    uow.state.bookings[60] = Booking(
        id=60,
        group_id=1,
        slot_id=50,
        start=SLOT_0900,
        end=end,
        status=status,
        contact=None,
        locale="nl",
        token_hash="h",
        created_at=NOW,
    )


@pytest.mark.parametrize(("field", "value"), [("slot_minutes", 20), ("timezone", "Europe/London")])
async def test_time_grid_is_frozen_while_future_bookings_exist(
    planning: PlanningService, uow: InMemoryUnitOfWork, field: str, value: object
) -> None:
    _future_booking(uow, BookingStatus.CONFIRMED)
    current = settings_of(uow.state.groups[1])

    with pytest.raises(DomainError, match=f"group_has_future_bookings: {field}"):
        await planning.update_settings(SUPER, "demo", replace(current, **{field: value}))
    # Other settings stay editable.
    assert await planning.update_settings(SUPER, "demo", replace(current, horizon_days=10))


async def test_cancelled_bookings_do_not_freeze_the_time_grid(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    _future_booking(uow, BookingStatus.CANCELLED)
    current = settings_of(uow.state.groups[1])

    assert await planning.update_settings(SUPER, "demo", replace(current, slot_minutes=20))


# --- opening hours and exceptions ----------------------------------------------------------


async def test_group_admin_manages_opening_hours_of_their_group(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    rule_id = await planning.add_rule(GROUP_ADMIN, "demo", _rule())
    schedule = await planning.schedule(GROUP_ADMIN, "demo")

    assert [(r.id, r.weekday, r.window.capacity) for r in schedule.rules] == [(rule_id, 0, 2)]

    await planning.remove_rule(GROUP_ADMIN, "demo", rule_id)

    assert (await planning.schedule(GROUP_ADMIN, "demo")).rules == []
    assert [(e.action, e.detail) for e in uow.state.audit] == [
        ("schedule.manage", "rule.add"),
        ("schedule.manage", "rule.remove"),
    ]


async def test_group_admin_changes_an_opening_window_in_place(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    rule_id = await planning.add_rule(GROUP_ADMIN, "demo", _rule(opens="09:00", closes="12:00"))

    changed = await planning.update_rule(
        GROUP_ADMIN, "demo", rule_id, _rule(opens="08:30", closes="12:30", capacity=5)
    )
    unchanged = await planning.update_rule(
        GROUP_ADMIN, "demo", rule_id, _rule(opens="08:30", closes="12:30", capacity=5)
    )

    assert changed
    assert not unchanged
    rules = (await planning.schedule(GROUP_ADMIN, "demo")).rules
    assert [(r.id, r.window) for r in rules] == [(rule_id, window("08:30", "12:30", 5))]
    assert [e.detail for e in uow.state.audit] == ["rule.add", "rule.edit"]


async def test_a_changed_window_may_overlap_only_its_own_old_times(
    planning: PlanningService,
) -> None:
    morning = await planning.add_rule(SUPER, "demo", _rule(opens="09:00", closes="12:00"))
    await planning.add_rule(SUPER, "demo", _rule(opens="13:00", closes="16:00"))

    # Growing into its own old times is fine; growing into the afternoon is not.
    await planning.update_rule(SUPER, "demo", morning, _rule(opens="10:00", closes="13:00"))
    with pytest.raises(DomainError, match="schedule_rules"):
        await planning.update_rule(SUPER, "demo", morning, _rule(opens="10:00", closes="14:00"))


async def test_changing_a_window_of_another_group_is_refused(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    rule_id = await planning.add_rule(SUPER, "andere-team", _rule())

    with pytest.raises(DomainError, match="not_permitted"):
        await planning.update_rule(GROUP_ADMIN, "andere-team", rule_id, _rule(capacity=9))
    with pytest.raises(DomainError, match="schedule_item_not_found"):
        await planning.update_rule(GROUP_ADMIN, "demo", rule_id, _rule(capacity=9))
    with pytest.raises(DomainError, match="schedule_item_not_found"):
        await planning.rule(GROUP_ADMIN, "demo", rule_id)
    assert uow.state.rules[2][0].window.capacity == 2


async def test_splitting_a_block_replaces_it_with_two_touching_blocks(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    rule = ScheduleRule(
        2, TimeWindow(time(9), time(17), 3, "Dag"), date(2026, 1, 1), date(2026, 6, 30)
    )
    rule_id = await planning.add_rule(GROUP_ADMIN, "demo", rule)

    second_id = await planning.split_rule(GROUP_ADMIN, "demo", rule_id, time(12), 0, "Lunch")

    rules = (await planning.schedule(GROUP_ADMIN, "demo")).rules
    assert [(r.id, r.window, r.window.label) for r in rules] == [
        (rule_id, window("09:00", "12:00", 3), "Dag"),
        (second_id, window("12:00", "17:00", 0), "Lunch"),
    ]
    # The second part keeps the weekday and validity period of the original.
    assert {(r.weekday, r.valid_from, r.valid_to) for r in rules} == {
        (2, date(2026, 1, 1), date(2026, 6, 30))
    }
    assert [e.detail for e in uow.state.audit] == ["rule.add", "rule.split"]


@pytest.mark.parametrize("at", [time(9), time(8), time(12), time(13)])
async def test_a_block_is_split_strictly_inside_it(
    planning: PlanningService, uow: InMemoryUnitOfWork, at: time
) -> None:
    rule_id = await planning.add_rule(SUPER, "demo", _rule(opens="09:00", closes="12:00"))

    with pytest.raises(DomainError, match="split_at"):
        await planning.split_rule(SUPER, "demo", rule_id, at, 1)

    assert [r.window for r in uow.state.rules[1]] == [window("09:00", "12:00", 2)]
    assert [e.detail for e in uow.state.audit] == ["rule.add"]


@pytest.mark.parametrize(
    ("capacity", "label", "field"),
    [
        (-1, None, "capacity"),
        (1001, None, "capacity"),
        (1, "x" * 51, "label"),
        (1, "a\nb", "label"),
    ],
)
async def test_the_second_part_is_validated_like_a_new_block(
    planning: PlanningService,
    uow: InMemoryUnitOfWork,
    capacity: int,
    label: str | None,
    field: str,
) -> None:
    rule_id = await planning.add_rule(SUPER, "demo", _rule(opens="09:00", closes="12:00"))

    with pytest.raises(DomainError, match=field):
        await planning.split_rule(SUPER, "demo", rule_id, time(10), capacity, label)

    assert [r.window for r in uow.state.rules[1]] == [window("09:00", "12:00", 2)]


async def test_splitting_a_block_of_another_group_is_refused(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    rule_id = await planning.add_rule(SUPER, "andere-team", _rule())

    with pytest.raises(DomainError, match="not_permitted"):
        await planning.split_rule(GROUP_ADMIN, "andere-team", rule_id, time(10), 1)
    with pytest.raises(DomainError, match="schedule_item_not_found"):
        await planning.split_rule(GROUP_ADMIN, "demo", rule_id, time(10), 1)
    assert [r.window for r in uow.state.rules[2]] == [window("09:00", "12:00", 2)]
    assert (await planning.schedule(GROUP_ADMIN, "demo")).rules == []


async def test_overlapping_opening_hours_are_refused(planning: PlanningService) -> None:
    await planning.add_rule(SUPER, "demo", _rule(opens="09:00", closes="12:00"))

    with pytest.raises(DomainError, match="schedule_rules"):
        await planning.add_rule(SUPER, "demo", _rule(opens="11:00", closes="13:00"))
    # Touching windows and other weekdays are fine.
    await planning.add_rule(SUPER, "demo", _rule(opens="12:00", closes="13:00"))
    await planning.add_rule(SUPER, "demo", _rule(weekday=1))


async def test_schedule_of_another_group_is_out_of_reach(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    rule_id = await planning.add_rule(SUPER, "andere-team", _rule())

    with pytest.raises(DomainError, match="not_permitted"):
        await planning.add_rule(GROUP_ADMIN, "andere-team", _rule(weekday=2))
    with pytest.raises(DomainError, match="not_permitted"):
        await planning.remove_rule(GROUP_ADMIN, "andere-team", rule_id)
    # A rule id of another group cannot be removed through one's own group either.
    with pytest.raises(DomainError, match="schedule_item_not_found"):
        await planning.remove_rule(GROUP_ADMIN, "demo", rule_id)
    assert [r.id for r in uow.state.rules[2]] == [rule_id]


async def test_exceptions_close_a_day_or_change_its_hours(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    closed = await planning.add_override(
        GROUP_ADMIN, "demo", ScheduleOverride(date(2026, 1, 6), None)
    )
    changed = await planning.add_override(
        GROUP_ADMIN, "demo", ScheduleOverride(date(2026, 1, 7), window("10:00", "11:00", 1))
    )
    schedule = await planning.schedule(GROUP_ADMIN, "demo")

    assert [(o.id, o.day, o.window) for o in schedule.overrides] == [
        (closed, date(2026, 1, 6), None),
        (changed, date(2026, 1, 7), window("10:00", "11:00", 1)),
    ]
    await planning.remove_override(GROUP_ADMIN, "demo", closed)
    assert [o.id for o in (await planning.schedule(GROUP_ADMIN, "demo")).overrides] == [changed]
    assert [e.detail for e in uow.state.audit] == [
        "override.add",
        "override.add",
        "override.remove",
    ]


async def test_exceptions_in_the_past_or_conflicting_are_refused(
    planning: PlanningService,
) -> None:
    with pytest.raises(DomainError, match="invalid_configuration: day"):
        await planning.add_override(
            SUPER, "demo", ScheduleOverride(TODAY - timedelta(days=1), None)
        )
    await planning.add_override(SUPER, "demo", ScheduleOverride(date(2026, 1, 6), None))
    with pytest.raises(DomainError, match="schedule_overrides"):
        await planning.add_override(
            SUPER, "demo", ScheduleOverride(date(2026, 1, 6), window("10:00", "11:00", 1))
        )


async def test_past_exceptions_are_not_listed(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.overrides[1] = [ScheduleOverride(date(2025, 12, 1), None, id=90)]

    assert (await planning.schedule(SUPER, "demo")).overrides == []


async def test_viewing_the_schedule_needs_access_to_the_group(planning: PlanningService) -> None:
    with pytest.raises(DomainError, match="not_permitted"):
        await planning.schedule(GROUP_ADMIN, "andere-team")
    with pytest.raises(DomainError, match="group_not_found"):
        await planning.schedule(SUPER, "nope")


async def test_preview_shows_the_slots_participants_will_see(planning: PlanningService) -> None:
    await planning.add_rule(
        SUPER, "demo", _rule(weekday=0, opens="09:00", closes="10:00", capacity=3)
    )

    days = await planning.preview(SUPER, "demo", days=7)

    amsterdam = uow_tz()
    assert [
        (d.day, [(s.start.astimezone(amsterdam).time(), s.capacity) for s in d.slots]) for d in days
    ] == [(date(2026, 1, 5), [(time(9), 3), (time(9, 30), 3)])]


async def test_capacity_change_never_touches_existing_slots(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    _future_booking(uow, BookingStatus.CONFIRMED)
    rule_id = await planning.add_rule(SUPER, "demo", _rule(capacity=2))
    await planning.remove_rule(SUPER, "demo", rule_id)
    await planning.add_rule(SUPER, "demo", _rule(capacity=1))

    assert uow.state.slots[50].capacity_snapshot == 2
    assert uow.state.bookings[60].status is BookingStatus.CONFIRMED


def test_rule_ids_are_not_part_of_equality() -> None:
    assert ScheduleRule(0, window(), id=1) == ScheduleRule(0, window(), id=2)


def uow_tz() -> ZoneInfo:
    return ZoneInfo("Europe/Amsterdam")


# --- archiving a group (ADR 0084) ------------------------------------------------------------


async def test_org_admin_archives_a_group_by_typing_its_name(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    await planning.archive_group(ORG_ADMIN, "demo", " demo ")

    assert uow.state.groups[1].archived_at == NOW
    assert audit_rows(uow) == [("o-1", "group.delete", Outcome.SUCCESS, "archived", "demo")]


async def test_archiving_needs_the_exact_name(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError, match="confirmation_mismatch: confirmation"):
        await planning.archive_group(ORG_ADMIN, "demo", "Demo-team")

    assert not uow.state.groups[1].is_archived
    assert audit_rows(uow) == []


async def test_archiving_is_refused_while_bookings_lie_ahead(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    _future_booking(uow, BookingStatus.CONFIRMED)

    with pytest.raises(DomainError, match="group_has_future_bookings"):
        await planning.archive_group(SUPER, "demo", "demo")
    assert await planning.future_bookings(SUPER, "demo") == 1

    uow.state.bookings[60] = replace(uow.state.bookings[60], status=BookingStatus.CANCELLED)
    await planning.archive_group(SUPER, "demo", "demo")
    assert uow.state.groups[1].is_archived


async def test_a_group_admin_cannot_archive_and_an_archived_group_refuses_changes(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError, match="not_permitted"):
        await planning.archive_group(GROUP_ADMIN, "demo", "demo")

    await planning.archive_group(ORG_ADMIN, "demo", "demo")

    with pytest.raises(DomainError, match="group_archived"):
        await planning.archive_group(ORG_ADMIN, "demo", "demo")
    with pytest.raises(DomainError, match="group_archived"):
        await planning.add_rule(GROUP_ADMIN, "demo", _rule())


async def test_an_archived_group_keeps_its_slug_and_leaves_the_list(
    planning: PlanningService, uow: InMemoryUnitOfWork
) -> None:
    await planning.archive_group(ORG_ADMIN, "demo", "demo")

    with pytest.raises(DomainError, match="slug_taken"):
        await planning.create_group(
            ORG_ADMIN, NewGroup("demo", "Demo", "demo", "Europe/Amsterdam", 20)
        )
    admin = AdminService(uow, FixedClock(NOW))
    assert [s.group.slug for s in await admin.groups(ORG_ADMIN)] == []
    assert [s.group.slug for s in await admin.groups(ORG_ADMIN, archived=True)] == ["demo"]
