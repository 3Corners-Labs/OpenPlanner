"""The usage.fact log line: allow-listed fields, a bucket for a duration, no person (ADR 0117)."""

from datetime import UTC, datetime

from app.domain.channels import ChannelKind
from app.domain.privacy import DATA_FIELDS, DERIVED_FIELDS, USAGE_FIELDS
from app.domain.usage import FactKind, UsageFact, UsageOutcome
from app.domain.usage_log import FACT_FIELD_NAMES, LOG_FIELDS, fact_fields

HOUR = datetime(2026, 11, 4, 10, tzinfo=UTC)


def test_the_line_holds_the_fact_s_codes_the_hour_and_a_bucket_never_the_seconds() -> None:
    fact = UsageFact(
        FactKind.BOOKING_CANCELLED,
        HOUR,
        3,
        7,
        channel_kind=ChannelKind.EMAIL,
        outcome=UsageOutcome.CANCELLED_BY_PLANNER,
        locale="nl",
        value_seconds=400,
    )

    assert fact_fields(fact) == {
        "kind": "booking.cancelled",
        "bucket_hour": "2026-11-04T10:00:00Z",
        "organisation_id": 3,
        "group_id": 7,
        "locale": "nl",
        "channel_kind": "email",
        "outcome": "cancelled_by_planner",
        "duration_bucket": 2,
    }


def test_a_signed_duration_keeps_its_mirrored_bucket() -> None:
    fact = UsageFact(FactKind.CALL_HOST_JOINED, HOUR, 1, 1, value_seconds=-120)

    assert fact_fields(fact)["duration_bucket"] == -2


def test_no_field_of_the_line_is_personal_data_or_something_the_fact_lacks() -> None:
    assert set(LOG_FIELDS) - {"duration_bucket"} <= FACT_FIELD_NAMES
    assert "value_seconds" not in LOG_FIELDS
    assert not set(LOG_FIELDS) & (set(DATA_FIELDS) | set(DERIVED_FIELDS))
    assert (set(LOG_FIELDS) - {"duration_bucket"}) <= set(USAGE_FIELDS)
