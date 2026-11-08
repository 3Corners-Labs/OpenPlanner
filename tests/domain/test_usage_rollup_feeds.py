"""Each fact kind of step 3 makes the cells the metric catalogue says (ADR 0117 point 6)."""

from datetime import UTC, datetime

from app.domain.booking import BookingKind
from app.domain.channels import ChannelKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import (
    METRICS,
    ActorClass,
    FactKind,
    UsageCell,
    UsageFact,
    UsageOutcome,
    duration_bucket,
)
from app.domain.usage_rollup import FILL_CLASSES, cells_of

HOUR = datetime(2026, 11, 9, 10, tzinfo=UTC)
SCHEDULED = BookingKind.SCHEDULED
DIRECT = BookingKind.DIRECT


def _fact(kind: FactKind, **extra: object) -> UsageFact:
    return UsageFact(kind, HOUR, 1, 2, **extra)  # type: ignore[arg-type]


def _by_metric(fact: UsageFact) -> dict[str, UsageCell]:
    cells = cells_of(fact)
    assert len({c.metric for c in cells}) == len(cells)
    return {c.metric: c for c in cells}


def test_every_fact_kind_makes_a_cell_for_the_metrics_that_list_it() -> None:
    samples = {
        FactKind.BOOKING_OUTCOME: _fact(FactKind.BOOKING_OUTCOME, outcome=UsageOutcome.COMPLETED),
        FactKind.REBOOK_PROPOSED: _fact(FactKind.REBOOK_PROPOSED, actor_class=ActorClass.PLANNER),
        FactKind.REBOOK_ACCEPTED: _fact(FactKind.REBOOK_ACCEPTED, value_seconds=900),
        FactKind.REBOOK_EXPIRED: _fact(FactKind.REBOOK_EXPIRED),
        FactKind.CALL_CREATED: _fact(
            FactKind.CALL_CREATED, booking_kind=DIRECT, participants=2, dispatch_kind=None
        ),
        FactKind.CALL_CONNECTED: _fact(FactKind.CALL_CONNECTED, value_seconds=30),
        FactKind.CALL_HOST_JOINED: _fact(
            FactKind.CALL_HOST_JOINED, booking_kind=SCHEDULED, value_seconds=-60
        ),
        FactKind.CALL_GUEST_WAITED: _fact(FactKind.CALL_GUEST_WAITED, value_seconds=60),
        FactKind.CALL_ENDED: _fact(FactKind.CALL_ENDED, value_seconds=600, participants=2),
        FactKind.CALL_WAITING_LEFT: _fact(
            FactKind.CALL_WAITING_LEFT, booking_kind=DIRECT, value_seconds=45, reason_code="waiting"
        ),
        FactKind.CALL_NOT_PICKED_UP: _fact(FactKind.CALL_NOT_PICKED_UP),
        FactKind.CALL_FAILED: _fact(FactKind.CALL_FAILED, reason_code="task.rejected"),
        FactKind.QUEUE_CLAIMED: _fact(FactKind.QUEUE_CLAIMED, value_seconds=120),
        FactKind.QUEUE_RELEASED: _fact(FactKind.QUEUE_RELEASED),
        FactKind.SLOT_CLOSED: _fact(FactKind.SLOT_CLOSED, capacity=4, booked=1),
        FactKind.SLOT_SHORTFALL: _fact(
            FactKind.SLOT_SHORTFALL, reason_code="open", capacity=1, booked=2
        ),
        FactKind.MESSAGE_SENT: _fact(
            FactKind.MESSAGE_SENT, channel_kind=ChannelKind.EMAIL, reason_code="reminder"
        ),
        FactKind.MESSAGE_FAILED: _fact(
            FactKind.MESSAGE_FAILED, channel_kind=ChannelKind.SMS, reason_code="reminder"
        ),
        FactKind.TRIAL_CREATED: _fact(FactKind.TRIAL_CREATED),
        FactKind.TRIAL_JOINED: _fact(FactKind.TRIAL_JOINED),
        FactKind.TRIAL_ENTERED: _fact(FactKind.TRIAL_ENTERED),
        FactKind.TRIAL_FAILED: _fact(FactKind.TRIAL_FAILED),
    }
    assert set(samples) | {k for k in FactKind if k.value.startswith("booking.")} >= set(FactKind)
    listed = {m.id: set(m.facts) for m in METRICS}
    for kind, fact in samples.items():
        cells = cells_of(fact)
        assert cells, kind
        assert all(kind in listed[c.metric] for c in cells), kind
        # Every metric that lists the kind and is not about a team member gets a cell, except the
        # ones that read a peak or a ratio's other half from a different kind.
    covered = {c.metric for fact in samples.values() for c in cells_of(fact)}
    for metric in METRICS:
        if not metric.staff and metric.id not in {"U1", "U4", "U5", "U6", "U9", "U10"}:
            assert metric.id in covered or metric.id in {"C4", "M2", "Q1", "U7"}, metric.id


def test_an_outcome_feeds_the_outcome_count_and_the_no_show_rate() -> None:
    cells = _by_metric(_fact(FactKind.BOOKING_OUTCOME, outcome=UsageOutcome.NO_SHOW))

    assert sorted(cells) == ["U2", "U3"]
    assert cells["U3"].outcome is UsageOutcome.NO_SHOW


def test_the_rebooking_funnel_has_a_stage_per_kind() -> None:
    proposed = _by_metric(_fact(FactKind.REBOOK_PROPOSED, actor_class=ActorClass.PLANNER))["U8"]
    accepted = _by_metric(_fact(FactKind.REBOOK_ACCEPTED, value_seconds=3600))["U8"]
    expired = _by_metric(_fact(FactKind.REBOOK_EXPIRED))["U8"]

    assert (proposed.reason_code, accepted.reason_code, expired.reason_code) == (
        "proposed",
        "accepted",
        "expired",
    )
    assert proposed.actor_class is ActorClass.PLANNER
    assert accepted.bucket == duration_bucket(3600)
    assert accepted.sum_value == 3600


def test_a_direct_call_creation_feeds_the_denominator_and_the_peak_load() -> None:
    cells = _by_metric(_fact(FactKind.CALL_CREATED, booking_kind=DIRECT, participants=3))

    assert sorted(cells) == ["Q6", "Q9"]
    assert cells["Q9"].max_value == 3
    assert cells["Q9"].sum_value == 3
    assert cells["Q6"].reason_code == "created"


def test_a_scheduled_call_creation_feeds_neither_the_direct_denominator_nor_the_load() -> None:
    assert cells_of(_fact(FactKind.CALL_CREATED, booking_kind=SCHEDULED)) == []


def test_a_task_wait_is_a_duration_and_a_missing_wait_makes_no_cell() -> None:
    (cell,) = cells_of(_fact(FactKind.CALL_CONNECTED, value_seconds=95))
    assert (cell.metric, cell.sum_value, cell.bucket) == ("Q2", 95, duration_bucket(95))
    assert cells_of(_fact(FactKind.CALL_CONNECTED)) == []


def test_host_punctuality_is_signed_and_only_for_scheduled_calls() -> None:
    (early,) = cells_of(_fact(FactKind.CALL_HOST_JOINED, booking_kind=SCHEDULED, value_seconds=-90))

    assert early.metric == "Q3"
    assert early.bucket == duration_bucket(-90, signed=True) < 0
    assert cells_of(_fact(FactKind.CALL_HOST_JOINED, booking_kind=DIRECT, value_seconds=5)) == []


def test_the_guest_wait_feeds_q4() -> None:
    (cell,) = cells_of(_fact(FactKind.CALL_GUEST_WAITED, value_seconds=240))
    assert cell.metric == "Q4"
    assert cell.bucket == duration_bucket(240)


def test_a_finished_call_feeds_its_length_and_its_participants() -> None:
    cells = _by_metric(
        _fact(FactKind.CALL_ENDED, booking_kind=SCHEDULED, value_seconds=1200, participants=3)
    )

    assert sorted(cells) == ["Q5", "U11"]
    assert cells["U11"].sum_value == 3
    assert cells["U11"].count == 1
    assert cells["Q5"].sum_value == 1200


def test_a_finished_call_without_a_length_still_counts_its_participants() -> None:
    cells = _by_metric(_fact(FactKind.CALL_ENDED, participants=2))
    assert sorted(cells) == ["U11"]


def test_leaving_a_direct_call_is_cut_by_when_the_participant_left() -> None:
    (cell,) = cells_of(
        _fact(
            FactKind.CALL_WAITING_LEFT, booking_kind=DIRECT, value_seconds=45, reason_code="agent"
        )
    )
    assert (cell.metric, cell.reason_code, cell.bucket) == ("Q6", "agent", duration_bucket(45))


def test_not_picked_up_is_counted_with_its_own_word() -> None:
    (cell,) = cells_of(_fact(FactKind.CALL_NOT_PICKED_UP, booking_kind=DIRECT))
    assert (cell.metric, cell.reason_code) == ("Q6", "not_picked_up")


def test_a_failure_is_counted_by_part_and_code() -> None:
    (cell,) = cells_of(_fact(FactKind.CALL_FAILED, reason_code="meeting.unauthorized"))
    assert (cell.metric, cell.reason_code) == ("Q8", "meeting.unauthorized")


def test_claims_and_releases_are_two_words_of_the_queue_metric() -> None:
    (claim,) = cells_of(_fact(FactKind.QUEUE_CLAIMED, value_seconds=300))
    (release,) = cells_of(_fact(FactKind.QUEUE_RELEASED))

    assert (claim.metric, claim.reason_code, claim.bucket) == (
        "Q7",
        "claimed",
        duration_bucket(300),
    )
    assert (release.metric, release.reason_code) == ("Q7", "released")


def test_a_closed_slot_gives_booked_and_capacity_idle_places_and_a_fill_class() -> None:
    cells = cells_of(_fact(FactKind.SLOT_CLOSED, capacity=4, booked=1))
    by_word = {(c.metric, c.reason_code): c for c in cells}

    assert by_word[("C1", "booked")].sum_value == 1
    assert by_word[("C1", "capacity")].sum_value == 4
    assert by_word[("C3", None)].sum_value == 3
    assert by_word[("C2", "under_half")].count == 1


def test_fill_classes_cover_empty_under_half_under_full_and_full() -> None:
    def fill(capacity: int, booked: int) -> str:
        cells = cells_of(_fact(FactKind.SLOT_CLOSED, capacity=capacity, booked=booked))
        return next(c.reason_code for c in cells if c.metric == "C2" and c.reason_code)

    assert [fill(4, 0), fill(4, 1), fill(4, 2), fill(4, 3), fill(4, 4), fill(2, 3)] == [
        "empty",
        "under_half",
        "under_full",
        "under_full",
        "full",
        "full",
    ]
    assert set(FILL_CLASSES) == {"empty", "under_half", "under_full", "full"}


def test_an_overbooked_slot_has_no_negative_idle_capacity() -> None:
    cells = {c.metric: c for c in cells_of(_fact(FactKind.SLOT_CLOSED, capacity=1, booked=2))}
    assert cells["C3"].sum_value == 0


def test_a_shortfall_is_cut_by_whether_it_was_open_accepted_or_resolved() -> None:
    (cell,) = cells_of(_fact(FactKind.SLOT_SHORTFALL, reason_code="accepted", capacity=1, booked=2))
    assert (cell.metric, cell.reason_code) == ("C5", "accepted")


def test_messages_are_cut_by_channel_and_by_purpose_and_state() -> None:
    (sent,) = cells_of(
        _fact(FactKind.MESSAGE_SENT, channel_kind=ChannelKind.EMAIL, reason_code="reminder")
    )
    (failed,) = cells_of(
        _fact(FactKind.MESSAGE_FAILED, channel_kind=ChannelKind.SMS, reason_code="reminder")
    )

    assert (sent.metric, sent.channel_kind, sent.reason_code) == (
        "M1",
        ChannelKind.EMAIL,
        "reminder.sent",
    )
    assert (failed.channel_kind, failed.reason_code) == (ChannelKind.SMS, "reminder.failed")


def test_trial_calls_have_a_word_per_step() -> None:
    words = {
        kind: cells_of(_fact(kind))[0].reason_code
        for kind in (
            FactKind.TRIAL_CREATED,
            FactKind.TRIAL_JOINED,
            FactKind.TRIAL_ENTERED,
            FactKind.TRIAL_FAILED,
        )
    }
    assert list(words.values()) == ["created", "joined", "entered", "failed"]


def test_dispatch_kind_is_a_cut_of_the_queue_metrics() -> None:
    (cell,) = cells_of(
        _fact(
            FactKind.CALL_CONNECTED,
            value_seconds=10,
            booking_kind=SCHEDULED,
            dispatch_kind=DispatchKind.CONTACT_CENTER,
        )
    )
    assert cell.dispatch_kind is DispatchKind.CONTACT_CENTER
