from datetime import date

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import (
    PhoneMode,
    ScheduleOverride,
    ScheduleRule,
    validate_schedule,
    windows_on,
)
from tests.support.domain import make_group, window

MONDAY = date(2026, 1, 5)


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
        ("name", ""),
        ("name", "   "),
        ("name", "x" * 201),
        ("name", "Team\nBcc: x@example.org"),
        ("wxcc_queue_id", ""),
        ("wxcc_queue_id", "q" * 129),
        ("wxcc_queue_id", "queue\n1"),
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slug", "a" * 64),
        ("slot_minutes", 5),
        ("slot_minutes", 480),
        ("horizon_days", 1),
        ("horizon_days", 366),
    ],
)
def test_group_accepts_boundary_values(field: str, value: object) -> None:
    make_group(**{field: value})


def test_zero_notice_needs_zero_dispatch_lead() -> None:
    make_group(min_notice_minutes=0, dispatch_lead_minutes=0)


def test_configuration_errors_name_the_field() -> None:
    cases = [
        (lambda: window("10:00", "09:00"), "window"),
        (lambda: window(capacity=0), "capacity"),
        (lambda: ScheduleRule(weekday=-1, window=window()), "weekday"),
        (
            lambda: ScheduleRule(
                weekday=0, window=window(), valid_from=date(2026, 2, 2), valid_to=date(2026, 2, 1)
            ),
            "valid_to",
        ),
    ]
    for build, field in cases:
        with pytest.raises(DomainError) as excinfo:
            build()
        assert excinfo.value.field == field


def test_configuration_accepts_boundary_values() -> None:
    window(capacity=1000)
    ScheduleRule(weekday=6, window=window())
    ScheduleRule(weekday=0, window=window(), valid_from=MONDAY, valid_to=MONDAY)


@pytest.mark.parametrize(
    ("first", "second", "overlap"),
    [
        ((None, date(2026, 3, 31)), (date(2026, 3, 31), None), True),  # share one day
        ((None, date(2026, 3, 30)), (date(2026, 3, 31), None), False),
        ((date(2026, 4, 1), None), (None, date(2026, 4, 1)), True),
        ((date(2026, 4, 2), None), (None, date(2026, 4, 1)), False),
        ((None, None), (date(2030, 1, 1), date(2030, 1, 2)), True),
        ((date(2030, 1, 1), date(2030, 1, 2)), (None, None), True),
    ],
)
def test_validity_period_overlap(
    first: tuple[date | None, date | None],
    second: tuple[date | None, date | None],
    overlap: bool,
) -> None:
    rules = [
        ScheduleRule(
            weekday=0, window=window("09:00", "12:00"), valid_from=first[0], valid_to=first[1]
        ),
        ScheduleRule(
            weekday=0, window=window("10:00", "13:00"), valid_from=second[0], valid_to=second[1]
        ),
    ]
    if overlap:
        with pytest.raises(DomainError):
            validate_schedule(rules, [])
    else:
        validate_schedule(rules, [])


def test_rules_on_different_weekdays_never_conflict() -> None:
    validate_schedule(
        [
            ScheduleRule(weekday=0, window=window("09:00", "12:00")),
            ScheduleRule(weekday=1, window=window("09:00", "12:00")),
        ],
        [],
    )


def test_overrides_on_different_days_never_conflict() -> None:
    validate_schedule(
        [],
        [
            ScheduleOverride(day=MONDAY, window=None),
            ScheduleOverride(day=date(2026, 1, 6), window=window()),
        ],
    )


def test_dispatch_lead_defaults_to_five_minutes() -> None:
    from datetime import timedelta

    assert make_group().dispatch_lead == timedelta(minutes=5)


@pytest.mark.parametrize("minutes", [-1, 61])
def test_dispatch_lead_bounds(minutes: int) -> None:
    with pytest.raises(DomainError) as excinfo:
        make_group(dispatch_lead_minutes=minutes, min_notice_minutes=120)

    assert excinfo.value.field == "dispatch_lead_minutes"


@pytest.mark.parametrize("minutes", [0, 60])
def test_dispatch_lead_boundary_values(minutes: int) -> None:
    make_group(dispatch_lead_minutes=minutes, min_notice_minutes=60)


def test_minimum_notice_may_not_be_shorter_than_the_dispatch_lead() -> None:
    # Otherwise a booking would be read-only the moment it is made.
    with pytest.raises(DomainError) as excinfo:
        make_group(dispatch_lead_minutes=15, min_notice_minutes=10)

    assert excinfo.value.field == "min_notice_minutes"
    make_group(dispatch_lead_minutes=15, min_notice_minutes=15)
