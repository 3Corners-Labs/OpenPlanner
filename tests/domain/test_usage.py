"""The facts and the metric catalogue of reporting on use (ADR 0117 step 1, abuse cases R3, R8)."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.booking import BookingKind
from app.domain.channels import ChannelKind
from app.domain.privacy import (
    ALIASES,
    DERIVED_FIELDS,
    PII_FIELDS,
    USAGE_FIELDS,
)
from app.domain.scheduling import DispatchKind
from app.domain.usage import (
    BUCKET_LABELS,
    FACT_HOUR,
    METRICS,
    ActorClass,
    Aggregation,
    FactKind,
    HourSource,
    StaffFact,
    UsageCell,
    UsageFact,
    UsageOutcome,
    bucket_label,
    duration_bucket,
    hour_of,
)

AMSTERDAM = timezone(timedelta(hours=1))
FORBIDDEN_FRAGMENTS = (
    "name",
    "email",
    "phone",
    "url",
    "token",
    "note",
    "answer",
    "reference",
    "booking_id",
    "claimed",
    "pseudonym",
)


def _fact(**extra: object) -> UsageFact:
    """Build a minimal valid fact, with ``extra`` keyword arguments added."""
    return UsageFact(
        FactKind.BOOKING_CREATED,
        hour_of(datetime(2026, 11, 9, 10, 41, 7, 123456, tzinfo=UTC)),
        1,
        2,
        **extra,  # type: ignore[arg-type]
    )


def test_the_hour_is_the_utc_hour_without_minutes_or_seconds() -> None:
    moment = datetime(2026, 11, 9, 11, 41, 7, 5, tzinfo=AMSTERDAM)

    assert hour_of(moment) == datetime(2026, 11, 9, 10, 0, tzinfo=UTC)


def test_a_moment_without_a_timezone_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        hour_of(datetime(2026, 11, 9, 10, 41))


def test_a_fact_holds_the_hour_never_the_exact_time() -> None:
    assert _fact().bucket_hour == datetime(2026, 11, 9, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2026, 11, 9, 10, 41, tzinfo=UTC),
        datetime(2026, 11, 9, 10, 0, 7, tzinfo=UTC),
        datetime(2026, 11, 9, 10, 0, 0, 5, tzinfo=UTC),
        datetime(2026, 11, 9, 10, 0),
        datetime(2026, 11, 9, 11, 0, tzinfo=AMSTERDAM),
    ],
)
def test_a_fact_built_directly_still_refuses_an_exact_time(moment: datetime) -> None:
    """The types check themselves, so read-back from storage passes the same rules."""
    with pytest.raises(ValueError, match="bucket_hour"):
        UsageFact(FactKind.BOOKING_CREATED, moment, 1, 2)
    with pytest.raises(ValueError, match="bucket_hour"):
        StaffFact(FactKind.CALL_ENDED, moment, 1, 2, staff_id=3)


def test_a_fact_carries_codes_and_numbers_only() -> None:
    fact = _fact(
        booking_kind=BookingKind.DIRECT,
        dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
        subject_code="demo.subject",
        locale="nl",
        channel_kind=ChannelKind.EMAIL,
        outcome=UsageOutcome.NO_SHOW,
        actor_class=ActorClass.PLANNER,
        reason_code="staffing",
        value_seconds=-90,
        participants=3,
        capacity=4,
        booked=2,
    )

    assert fact.value_seconds == -90
    assert fact.locale == "nl"


@pytest.mark.parametrize(
    "bad",
    [
        "has space",
        "a@b.nl",
        "Jan Jansen",
        "x" * 41,
        "",
        "UPPER",
        "semi;colon",
        "ünïcode",
        "0612345678",
        "12-34",
    ],
)
@pytest.mark.parametrize("field", ["subject_code", "reason_code"])
def test_a_code_is_never_free_text(field: str, bad: str) -> None:
    with pytest.raises(ValueError, match=field):
        _fact(**{field: bad})


@pytest.mark.parametrize("bad", ["", "nederlands", "n l", "nl_NL", "x" * 20])
def test_a_locale_is_a_short_language_tag(bad: str) -> None:
    with pytest.raises(ValueError, match="locale"):
        _fact(locale=bad)


@pytest.mark.parametrize("locale", ["nl", "en", "de", "fr-be", "pt-br"])
def test_a_language_tag_is_accepted(locale: str) -> None:
    assert _fact(locale=locale).locale == locale


@pytest.mark.parametrize("field", ["participants", "capacity", "booked"])
def test_a_count_is_not_negative(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _fact(**{field: -1})


def test_only_a_duration_may_be_negative() -> None:
    assert _fact(value_seconds=-3600).value_seconds == -3600


def test_a_fact_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        _fact().group_id = 9  # type: ignore[misc]


def test_a_staff_fact_names_the_team_member_by_row_id_only() -> None:
    fact = StaffFact(
        FactKind.CALL_ENDED,
        hour_of(datetime(2026, 11, 9, 10, 41, tzinfo=UTC)),
        1,
        2,
        staff_id=7,
        value_seconds=900,
    )

    assert fact.staff_id == 7
    assert fact.bucket_hour.minute == 0


def test_a_staff_id_is_a_positive_row_id() -> None:
    with pytest.raises(ValueError, match="staff_id"):
        StaffFact(
            FactKind.CALL_ENDED,
            hour_of(datetime(2026, 11, 9, 10, 41, tzinfo=UTC)),
            1,
            2,
            staff_id=0,
        )


def test_the_allow_list_is_exactly_the_fields_of_both_fact_shapes() -> None:
    declared = {f.name for f in fields(UsageFact)} | {f.name for f in fields(StaffFact)}

    assert declared == set(USAGE_FIELDS)


def test_the_general_fact_has_no_staff_id() -> None:
    assert "staff_id" not in {f.name for f in fields(UsageFact)}


def test_no_usage_field_is_a_personal_data_field() -> None:
    """R3: the mirror of the test that the purge works from the lists."""
    used = set(USAGE_FIELDS)

    assert not used & PII_FIELDS
    assert not used & set(DERIVED_FIELDS)
    assert not used & ALIASES


@pytest.mark.parametrize("field", USAGE_FIELDS)
def test_no_usage_field_looks_like_an_identifier(field: str) -> None:
    assert not any(fragment in field for fragment in FORBIDDEN_FRAGMENTS)


def test_every_fact_kind_has_one_hour_source() -> None:
    assert set(FACT_HOUR) == set(FactKind)


def test_outcome_and_capacity_facts_are_keyed_on_the_slot_start() -> None:
    """ADR 0117 point 3: the cohort of point 6 is the slot start, so no query holds its own rule."""
    slot_keyed = {kind for kind, source in FACT_HOUR.items() if source is HourSource.SLOT_START}

    assert {
        FactKind.BOOKING_OUTCOME,
        FactKind.BOOKING_CANCELLED,
        FactKind.BOOKING_CONFIRM_EXPIRED,
        FactKind.SLOT_CLOSED,
        FactKind.SLOT_SHORTFALL,
        FactKind.BOOKING_SLOTTED,
    } <= slot_keyed


def test_bookings_created_are_keyed_on_the_creation_hour() -> None:
    assert FACT_HOUR[FactKind.BOOKING_CREATED] is HourSource.CREATION


def test_the_catalogue_has_thirty_metrics_with_unique_ids() -> None:
    ids = [m.id for m in METRICS]

    assert len(ids) == len(set(ids)) == 30
    assert {i for i in ids if i.startswith("U")} == {f"U{n}" for n in range(1, 12)}
    assert {i for i in ids if i.startswith("Q")} == {f"Q{n}" for n in range(1, 10)}
    assert {i for i in ids if i.startswith("C")} == {f"C{n}" for n in range(1, 6)}
    assert {i for i in ids if i.startswith("S")} == {"S1", "S2"}
    assert {i for i in ids if i.startswith("M")} == {"M1", "M2"}
    assert {i for i in ids if i.startswith("T")} == {"T1"}


def test_every_metric_states_its_definition_facts_and_aggregation() -> None:
    for metric in METRICS:
        assert metric.name
        assert metric.definition
        assert metric.facts
        assert isinstance(metric.aggregation, Aggregation)
        assert all(isinstance(kind, FactKind) for kind in metric.facts)


def test_no_fact_kind_is_written_for_nothing() -> None:
    used = {kind for metric in METRICS for kind in metric.facts}

    assert used == set(FactKind)


def test_only_the_team_metrics_are_marked_staff() -> None:
    assert {m.id for m in METRICS if m.staff} == {"S1", "S2"}


def test_the_peak_metric_aggregates_by_maximum() -> None:
    by_id = {m.id: m for m in METRICS}

    assert by_id["Q9"].aggregation is Aggregation.MAX
    assert by_id["U1"].aggregation is Aggregation.COUNT


@pytest.mark.parametrize(
    ("seconds", "bucket"),
    [
        (0, 0),
        (59, 0),
        (60, 1),
        (299, 1),
        (300, 2),
        (900, 3),
        (1800, 4),
        (3600, 5),
        (7200, 6),
        (21600, 7),
        (86400, 8),
        (604799, 8),
        (604800, 9),
        (10**9, 9),
    ],
)
def test_a_duration_falls_in_a_fixed_bucket(seconds: int, bucket: int) -> None:
    assert duration_bucket(seconds) == bucket


def test_there_are_ten_buckets_with_a_label_each() -> None:
    assert len(BUCKET_LABELS) == 10
    assert bucket_label(0) == "0-1 min"
    assert bucket_label(9) == "over 7 d"


def test_an_unsigned_duration_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        duration_bucket(-1)


@pytest.mark.parametrize(
    ("seconds", "code"), [(-1, -1), (-61, -2), (-604800, -10), (30, 0), (61, 1)]
)
def test_a_signed_duration_is_mirrored_around_zero(seconds: int, code: int) -> None:
    assert duration_bucket(seconds, signed=True) == code


def test_a_negative_bucket_has_its_own_label() -> None:
    assert bucket_label(-2) != bucket_label(1)
    assert bucket_label(-2).startswith("-")


def test_a_bucket_code_outside_the_range_has_no_label() -> None:
    with pytest.raises(ValueError, match="bucket"):
        bucket_label(10)


def test_a_rollup_cell_is_a_frozen_typed_row() -> None:
    cell = UsageCell(
        bucket_hour=datetime(2026, 11, 9, 10, tzinfo=UTC),
        organisation_id=1,
        group_id=2,
        metric="U1",
        count=4,
        sum_value=0,
        max_value=0,
    )

    assert replace(cell, count=5).count == 5
    with pytest.raises(FrozenInstanceError):
        cell.count = 9  # type: ignore[misc]
    assert cell.outcome is None
    assert cell.bucket is None
