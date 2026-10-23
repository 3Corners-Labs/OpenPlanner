from datetime import date, time

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import (
    Group,
    PhoneMode,
    ScheduleOverride,
    ScheduleRule,
    TimeWindow,
    validate_schedule,
    windows_on,
)

MONDAY = date(2026, 1, 5)


def window(opens: str, closes: str, capacity: int = 2) -> TimeWindow:
    return TimeWindow(time.fromisoformat(opens), time.fromisoformat(closes), capacity)


def make_group(**overrides: object) -> Group:
    fields: dict[str, object] = {
        "id": 1,
        "organisation_id": 1,
        "name": "Aangifte",
        "slug": "aangifte-2026",
        "timezone": "Europe/Amsterdam",
        "slot_minutes": 30,
    }
    fields.update(overrides)
    return Group(**fields)  # type: ignore[arg-type]


def test_group_defaults() -> None:
    group = make_group()

    assert group.phone_mode is PhoneMode.OPTIONAL
    assert group.min_notice_minutes == 60
    assert group.horizon_days == 60
    assert str(group.tz) == "Europe/Amsterdam"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slug", "Aangifte 2026"),
        ("slug", "-leading"),
        ("slug", "a" * 65),
        ("slug", ""),
        ("timezone", "Mars/Olympus"),
        ("timezone", "../../etc/localtime"),
        ("slot_minutes", 0),
        ("slot_minutes", 7),
        ("slot_minutes", 485),
        ("min_notice_minutes", -1),
        ("horizon_days", 0),
        ("horizon_days", 367),
    ],
)
def test_group_rejects_invalid_configuration(field: str, value: object) -> None:
    with pytest.raises(DomainError) as excinfo:
        make_group(**{field: value})

    assert excinfo.value.code is ErrorCode.INVALID_CONFIGURATION
    assert excinfo.value.field == field


@pytest.mark.parametrize(
    ("opens", "closes", "capacity"),
    [("10:00", "09:00", 1), ("09:00", "09:00", 1), ("09:00", "10:00", 0), ("09:00", "10:00", 1001)],
)
def test_time_window_rejects_invalid_values(opens: str, closes: str, capacity: int) -> None:
    with pytest.raises(DomainError) as excinfo:
        window(opens, closes, capacity)

    assert excinfo.value.code is ErrorCode.INVALID_CONFIGURATION


def test_rule_validation() -> None:
    with pytest.raises(DomainError, match="invalid_configuration"):
        ScheduleRule(weekday=7, window=window("09:00", "12:00"))
    with pytest.raises(DomainError, match="invalid_configuration"):
        ScheduleRule(
            weekday=0,
            window=window("09:00", "12:00"),
            valid_from=date(2026, 2, 1),
            valid_to=date(2026, 1, 1),
        )


def test_rule_applies_on_its_weekday_within_validity() -> None:
    rule = ScheduleRule(
        weekday=0,
        window=window("09:00", "12:00"),
        valid_from=date(2026, 1, 5),
        valid_to=date(2026, 1, 12),
    )

    assert rule.applies_on(date(2026, 1, 5))
    assert rule.applies_on(date(2026, 1, 12))
    assert not rule.applies_on(date(2026, 1, 6))  # Tuesday
    assert not rule.applies_on(date(2025, 12, 29))  # Monday before validity
    assert not rule.applies_on(date(2026, 1, 19))  # Monday after validity


def test_windows_on_uses_rules_sorted_by_opening() -> None:
    rules = [
        ScheduleRule(weekday=0, window=window("13:00", "16:00")),
        ScheduleRule(weekday=0, window=window("09:00", "12:00")),
        ScheduleRule(weekday=1, window=window("09:00", "17:00")),
    ]

    assert windows_on(MONDAY, rules, []) == [window("09:00", "12:00"), window("13:00", "16:00")]


def test_override_replaces_rules_for_that_day() -> None:
    rules = [ScheduleRule(weekday=0, window=window("09:00", "17:00"))]
    overrides = [ScheduleOverride(day=MONDAY, window=window("10:00", "11:00", 1))]

    assert windows_on(MONDAY, rules, overrides) == [window("10:00", "11:00", 1)]
    assert windows_on(date(2026, 1, 12), rules, overrides) == [window("09:00", "17:00")]


def test_closed_override_closes_the_day() -> None:
    rules = [ScheduleRule(weekday=0, window=window("09:00", "17:00"))]

    assert windows_on(MONDAY, rules, [ScheduleOverride(day=MONDAY, window=None)]) == []


def test_validate_schedule_accepts_adjacent_windows() -> None:
    validate_schedule(
        [
            ScheduleRule(weekday=0, window=window("09:00", "12:00")),
            ScheduleRule(weekday=0, window=window("12:00", "15:00")),
        ],
        [],
    )


def test_validate_schedule_rejects_overlapping_rules() -> None:
    with pytest.raises(DomainError) as excinfo:
        validate_schedule(
            [
                ScheduleRule(weekday=0, window=window("09:00", "12:00")),
                ScheduleRule(weekday=0, window=window("11:00", "13:00")),
            ],
            [],
        )

    assert excinfo.value.field == "schedule_rules"


def test_overlapping_windows_in_disjoint_validity_periods_are_fine() -> None:
    validate_schedule(
        [
            ScheduleRule(weekday=0, window=window("09:00", "12:00"), valid_to=date(2026, 3, 31)),
            ScheduleRule(weekday=0, window=window("10:00", "13:00"), valid_from=date(2026, 4, 1)),
        ],
        [],
    )


def test_open_ended_validity_periods_overlap() -> None:
    with pytest.raises(DomainError):
        validate_schedule(
            [
                ScheduleRule(weekday=0, window=window("09:00", "12:00"), valid_to=None),
                ScheduleRule(
                    weekday=0, window=window("10:00", "13:00"), valid_from=date(2030, 1, 1)
                ),
            ],
            [],
        )


def test_validate_schedule_rejects_overlapping_overrides() -> None:
    with pytest.raises(DomainError) as excinfo:
        validate_schedule(
            [],
            [
                ScheduleOverride(day=MONDAY, window=window("09:00", "12:00")),
                ScheduleOverride(day=MONDAY, window=window("11:00", "13:00")),
            ],
        )

    assert excinfo.value.field == "schedule_overrides"


def test_validate_schedule_rejects_closed_day_with_windows() -> None:
    with pytest.raises(DomainError):
        validate_schedule(
            [],
            [
                ScheduleOverride(day=MONDAY, window=None),
                ScheduleOverride(day=MONDAY, window=window("09:00", "12:00")),
            ],
        )
