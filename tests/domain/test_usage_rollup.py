"""Facts become hourly cells per metric and cut, and the same facts always give the same cells."""

from datetime import UTC, datetime

import pytest

from app.domain.booking import BookingKind
from app.domain.channels import ChannelKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import (
    METRICS,
    ActorClass,
    FactKind,
    StaffFact,
    UsageCell,
    UsageFact,
    UsageOutcome,
    duration_bucket,
)
from app.domain.usage_rollup import cells_of, merge, roll

HOUR = datetime(2026, 11, 9, 10, tzinfo=UTC)
NEXT_HOUR = datetime(2026, 11, 9, 11, tzinfo=UTC)
METRIC_FACTS = {m.id: set(m.facts) for m in METRICS}


def _fact(kind: FactKind, **extra: object) -> UsageFact:
    return UsageFact(kind, extra.pop("at", HOUR), 1, 2, **extra)  # type: ignore[arg-type]


def _metrics(cells: list[UsageCell]) -> list[str]:
    return sorted(c.metric for c in cells)


def test_a_new_booking_feeds_volume_lead_time_queue_method_and_locale() -> None:
    fact = _fact(
        FactKind.BOOKING_CREATED,
        booking_kind=BookingKind.SCHEDULED,
        dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
        locale="nl",
        value_seconds=3 * 3600,
    )

    cells = {c.metric: c for c in cells_of(fact)}

    assert sorted(cells) == ["M2", "Q1", "U1", "U6"]
    assert cells["U6"].sum_value == cells["U6"].max_value == 3 * 3600
    assert cells["U6"].bucket == duration_bucket(3 * 3600)
    assert cells["Q1"].dispatch_kind is DispatchKind.OPENPLANNER_QUEUE
    assert cells["Q1"].booking_kind is BookingKind.SCHEDULED
    assert cells["M2"].locale == "nl"
    assert cells["U1"].locale is None


def test_a_booking_created_by_a_move_is_no_new_booking() -> None:
    assert cells_of(_fact(FactKind.BOOKING_CREATED, reason_code="move", value_seconds=60)) == []


def test_a_placement_feeds_the_busiest_hours() -> None:
    assert _metrics(cells_of(_fact(FactKind.BOOKING_SLOTTED))) == ["C4"]


def test_a_confirmation_and_a_lapse_both_feed_the_conversion() -> None:
    confirmed = cells_of(_fact(FactKind.BOOKING_CONFIRMED, value_seconds=600))
    lapsed = cells_of(
        _fact(
            FactKind.BOOKING_CONFIRM_EXPIRED,
            outcome=UsageOutcome.EXPIRED_UNCONFIRMED,
            value_seconds=90_000,
        )
    )

    assert _metrics(confirmed) == ["U9"]
    assert confirmed[0].outcome is None
    assert _metrics(lapsed) == ["U2", "U9"]
    assert {c.outcome for c in lapsed} == {UsageOutcome.EXPIRED_UNCONFIRMED}


def test_a_participants_cancellation_feeds_outcome_rate_and_lateness() -> None:
    fact = _fact(
        FactKind.BOOKING_CANCELLED,
        actor_class=ActorClass.PARTICIPANT,
        outcome=UsageOutcome.CANCELLED_BY_PARTICIPANT,
        value_seconds=-1800,
    )

    cells = {c.metric: c for c in cells_of(fact)}

    assert sorted(cells) == ["U2", "U4", "U5"]
    assert cells["U5"].bucket == duration_bucket(-1800, signed=True)
    assert cells["U2"].outcome is UsageOutcome.CANCELLED_BY_PARTICIPANT


def test_a_cancellation_by_someone_else_is_only_an_outcome() -> None:
    fact = _fact(
        FactKind.BOOKING_CANCELLED,
        actor_class=ActorClass.PLANNER,
        outcome=UsageOutcome.CANCELLED_BY_PLANNER,
        reason_code="staffing",
    )

    assert _metrics(cells_of(fact)) == ["U2"]


def test_a_move_feeds_reschedules_by_actor_and_reason() -> None:
    (cell,) = cells_of(_fact(FactKind.BOOKING_MOVED, actor_class=ActorClass.PLANNER))

    assert (cell.metric, cell.actor_class) == ("U7", ActorClass.PLANNER)


@pytest.mark.parametrize(
    ("kind", "word"),
    [(FactKind.BOOKING_SUSPENDED, "suspended"), (FactKind.BOOKING_RESUMED, "resumed")],
)
def test_a_closure_change_says_which_way_in_the_reason_cut(kind: FactKind, word: str) -> None:
    (cell,) = cells_of(_fact(kind, actor_class=ActorClass.CLOSURE))

    assert (cell.metric, cell.reason_code) == ("U10", word)


def test_a_fact_without_a_duration_has_no_bucket() -> None:
    cells = {c.metric: c for c in cells_of(_fact(FactKind.BOOKING_CREATED))}

    assert cells["U6"].bucket is None
    assert cells["U6"].sum_value == 0


def test_every_cell_names_a_metric_that_lists_its_fact() -> None:
    """The catalogue and the rollup agree: a fact never feeds a metric that does not name it."""
    samples = [
        _fact(FactKind.BOOKING_CREATED, value_seconds=60, locale="nl"),
        _fact(FactKind.BOOKING_SLOTTED),
        _fact(FactKind.BOOKING_CONFIRMED, value_seconds=60),
        _fact(FactKind.BOOKING_CONFIRM_EXPIRED, outcome=UsageOutcome.EXPIRED_UNCONFIRMED),
        _fact(
            FactKind.BOOKING_CANCELLED,
            actor_class=ActorClass.PARTICIPANT,
            outcome=UsageOutcome.CANCELLED_BY_PARTICIPANT,
            value_seconds=60,
        ),
        _fact(FactKind.BOOKING_MOVED, actor_class=ActorClass.PARTICIPANT),
        _fact(FactKind.BOOKING_SUSPENDED, actor_class=ActorClass.CLOSURE),
        _fact(FactKind.BOOKING_RESUMED, actor_class=ActorClass.CLOSURE),
    ]
    for fact in samples:
        for cell in cells_of(fact):
            assert fact.kind in METRIC_FACTS[cell.metric], (fact.kind, cell.metric)


def test_cells_of_the_same_hour_and_cut_are_added_up() -> None:
    first = cells_of(_fact(FactKind.BOOKING_SLOTTED))
    second = cells_of(_fact(FactKind.BOOKING_SLOTTED))

    (cell,) = merge(first + second)

    assert cell.count == 2


def test_the_sum_adds_and_the_maximum_stays_the_largest() -> None:
    small = cells_of(_fact(FactKind.BOOKING_CONFIRMED, value_seconds=900))
    large = cells_of(_fact(FactKind.BOOKING_CONFIRMED, value_seconds=7200))

    (small_cell,) = [c for c in merge(small + large) if c.bucket == duration_bucket(900)]
    (large_cell,) = [c for c in merge(small + large) if c.bucket == duration_bucket(7200)]

    assert (small_cell.count, small_cell.sum_value, small_cell.max_value) == (1, 900, 900)
    assert (large_cell.count, large_cell.sum_value, large_cell.max_value) == (1, 7200, 7200)


def test_the_same_bucket_adds_its_exact_sum() -> None:
    cells = merge(
        cells_of(_fact(FactKind.BOOKING_CONFIRMED, value_seconds=1000))
        + cells_of(_fact(FactKind.BOOKING_CONFIRMED, value_seconds=1500))
    )

    (cell,) = cells
    assert (cell.count, cell.sum_value, cell.max_value) == (2, 2500, 1500)


def test_other_hours_groups_and_cuts_stay_apart() -> None:
    cells = merge(
        cells_of(_fact(FactKind.BOOKING_SLOTTED))
        + cells_of(_fact(FactKind.BOOKING_SLOTTED, at=NEXT_HOUR))
        + cells_of(UsageFact(FactKind.BOOKING_SLOTTED, HOUR, 1, 3))
        + cells_of(_fact(FactKind.BOOKING_CANCELLED, outcome=UsageOutcome.CANCELLED_BY_SYSTEM))
        + cells_of(_fact(FactKind.BOOKING_CANCELLED, outcome=UsageOutcome.CANCELLED_BY_PLANNER))
    )

    assert len(cells) == 5


def test_rolling_the_same_facts_twice_in_any_order_gives_the_same_cells() -> None:
    facts = [
        _fact(FactKind.BOOKING_CREATED, value_seconds=3600, locale="nl"),
        _fact(FactKind.BOOKING_SLOTTED),
        _fact(FactKind.BOOKING_CONFIRMED, value_seconds=900),
        _fact(FactKind.BOOKING_CREATED, value_seconds=7200, locale="en"),
    ]

    forward = roll(facts)
    backward = roll(list(reversed(facts)))

    assert sorted(forward, key=repr) == sorted(backward, key=repr)


def test_a_team_member_fact_is_not_rolled_here() -> None:
    staff = StaffFact(FactKind.CALL_ENDED, HOUR, 1, 2, staff_id=7, value_seconds=60)

    assert roll([staff, _fact(FactKind.BOOKING_SLOTTED)]) == roll([_fact(FactKind.BOOKING_SLOTTED)])


def test_a_message_channel_is_not_a_cut_of_a_booking_metric() -> None:
    fact = _fact(FactKind.BOOKING_CREATED, channel_kind=ChannelKind.EMAIL, locale="nl")

    assert all(c.channel_kind is None for c in cells_of(fact))
