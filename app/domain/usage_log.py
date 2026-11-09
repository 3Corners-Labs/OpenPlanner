"""A usage fact as a log event for the customer's log platform (ADR 0117 point 15).

With ``reporting_usage_events_to_log`` every non-team fact is also written as one line
``usage.fact`` after its transaction commits, through the same stdout line the log destinations
of ADR 0085 tap. The line holds the allow-listed fields of the fact (``privacy.USAGE_FIELDS``)
with two reductions: the time is the UTC hour already (a fact never holds more), and the exact
number of seconds becomes the duration bucket, because the customer keeps these lines for as
long as they like and the portal's own rule is that a duration is only ever known in buckets
outside the rollup. It holds no personal data, so the masking processor has nothing to mask.

    UsageFact ─► ``fact_fields`` ─► log.info("usage.fact", **fields) ─► stdout ─► destinations
"""

from dataclasses import fields
from datetime import UTC

from app.domain.usage import UsageFact, duration_bucket

_CODES = (
    "booking_kind",
    "dispatch_kind",
    "subject_code",
    "locale",
    "channel_kind",
    "outcome",
    "actor_class",
    "reason_code",
)
"""The code fields of a fact, written by their value."""

_COUNTS = ("participants", "capacity", "booked")
"""The count fields of a fact, written as they are."""


def fact_fields(fact: UsageFact) -> dict[str, object]:
    """Return the fields of the ``usage.fact`` line for ``fact``; empty fields are left out."""
    out: dict[str, object] = {
        "kind": fact.kind.value,
        "bucket_hour": fact.bucket_hour.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "organisation_id": fact.organisation_id,
        "group_id": fact.group_id,
    }
    for name in _CODES:
        value = getattr(fact, name)
        if value is not None:
            out[name] = getattr(value, "value", value)
    for name in _COUNTS:
        value = getattr(fact, name)
        if value is not None:
            out[name] = value
    if fact.value_seconds is not None:
        out["duration_bucket"] = duration_bucket(fact.value_seconds, signed=True)
    return out


LOG_FIELDS: tuple[str, ...] = tuple(
    ["kind", "bucket_hour", "organisation_id", "group_id", *_CODES, *_COUNTS, "duration_bucket"]
)
"""Every field a ``usage.fact`` line may carry, for the log register and its test."""

FACT_FIELD_NAMES: frozenset[str] = frozenset(f.name for f in fields(UsageFact))
"""The fields of a fact itself, to prove the line adds nothing the fact does not have."""
