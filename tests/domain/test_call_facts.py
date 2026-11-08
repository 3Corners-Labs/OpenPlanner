"""The facts a call event adds, from the status before and after it (ADR 0117 step 3)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.booking import BookingKind
from app.domain.booking_facts import BookingFacts
from app.domain.call_facts import (
    FAILURE_CODES,
    FIRST_ONLY,
    PRODUCES,
    call_facts,
    failure_code,
    needs_load,
)
from app.domain.calls import CallStatus, EventKind, apply_event, new_status
from app.domain.scheduling import DispatchKind
from app.domain.usage import ActorClass, FactKind, hour_of

START = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
T0 = START - timedelta(minutes=5)


def _b(kind: BookingKind = BookingKind.SCHEDULED) -> BookingFacts:
    return BookingFacts(
        1, 2, kind, DispatchKind.CONTACT_CENTER, "nl", START - timedelta(days=2), START
    )


def _step(status: CallStatus, kind: EventKind, at: datetime) -> tuple[CallStatus, CallStatus]:
    return status, apply_event(status, kind, at)


def test_a_work_item_is_one_call_created() -> None:
    before, after = _step(new_status(1), EventKind.TASK_CREATED, T0)

    (fact,) = call_facts(EventKind.TASK_CREATED, _b(), before, after, T0)

    assert (fact.kind, fact.bucket_hour) == (FactKind.CALL_CREATED, hour_of(T0))
    assert fact.reason_code is None
    assert fact.participants is None


def test_a_second_task_event_is_no_new_call() -> None:
    created = apply_event(new_status(1), EventKind.TASK_CREATED, T0)
    before, after = _step(created, EventKind.TASK_CREATED, T0)

    assert call_facts(EventKind.TASK_CREATED, _b(), before, after, T0) == []


def test_the_own_queue_fallback_is_a_call_created_with_its_reason_once() -> None:
    before, after = _step(new_status(1), EventKind.FALLBACK_QUEUED, T0)

    (fact,) = call_facts(EventKind.FALLBACK_QUEUED, _b(), before, after, T0)
    again = call_facts(EventKind.FALLBACK_QUEUED, _b(), after, after, T0)

    assert fact.reason_code == "fallback"
    assert again == []


def test_a_direct_call_carries_the_load() -> None:
    before, after = _step(new_status(1), EventKind.TASK_CREATED, T0)
    direct = _b(BookingKind.DIRECT)

    (fact,) = call_facts(EventKind.TASK_CREATED, direct, before, after, T0, load=3)

    assert needs_load(EventKind.TASK_CREATED, direct)
    assert not needs_load(EventKind.TASK_CREATED, _b())
    assert fact.participants == 3


def test_connecting_carries_the_wait_since_the_task_was_created() -> None:
    queued = apply_event(new_status(1), EventKind.TASK_CREATED, T0)
    at = T0 + timedelta(seconds=95)
    before, after = _step(queued, EventKind.TASK_CONNECTED, at)

    (fact,) = call_facts(EventKind.TASK_CONNECTED, _b(), before, after, at)
    again = call_facts(EventKind.TASK_CONNECTED, _b(), after, after, at)

    assert (fact.kind, fact.value_seconds) == (FactKind.CALL_CONNECTED, 95)
    assert again == []


def test_connecting_without_a_known_creation_has_no_wait() -> None:
    before, after = _step(new_status(1), EventKind.TASK_CONNECTED, T0)

    (fact,) = call_facts(EventKind.TASK_CONNECTED, _b(), before, after, T0)

    assert fact.value_seconds is None


@pytest.mark.parametrize(("offset", "expected"), [(-120, -120), (45, 45)])
def test_the_host_is_early_or_late_by_a_signed_number_of_seconds(
    offset: int, expected: int
) -> None:
    at = START + timedelta(seconds=offset)
    before, after = _step(new_status(1), EventKind.HOST_JOINED, at)

    (fact, *rest) = call_facts(EventKind.HOST_JOINED, _b(), before, after, at)

    assert (fact.kind, fact.value_seconds) == (FactKind.CALL_HOST_JOINED, expected)
    assert rest == []  # no guest was seen first


def test_a_guest_who_waited_makes_the_participant_wait_fact() -> None:
    guest = apply_event(new_status(1), EventKind.GUEST_JOINED, START - timedelta(minutes=2))
    at = START + timedelta(minutes=3)
    before, after = _step(guest, EventKind.HOST_JOINED, at)

    kinds = {
        f.kind: f.value_seconds for f in call_facts(EventKind.HOST_JOINED, _b(), before, after, at)
    }

    # The wait counts from the slot start, not from the early arrival of the guest.
    assert kinds == {FactKind.CALL_HOST_JOINED: 180, FactKind.CALL_GUEST_WAITED: 180}


def test_a_guest_who_came_after_the_host_did_not_wait() -> None:
    host = apply_event(new_status(1), EventKind.HOST_JOINED, START)
    before = host
    after = apply_event(host, EventKind.GUEST_JOINED, START + timedelta(minutes=1))

    assert call_facts(EventKind.GUEST_JOINED, _b(), before, after, START) == []
    assert all(
        f.kind is not FactKind.CALL_GUEST_WAITED
        for f in call_facts(EventKind.HOST_JOINED, _b(), new_status(1), host, START)
    )


def test_a_direct_call_has_no_punctuality_and_no_guest_wait() -> None:
    guest = apply_event(new_status(1), EventKind.GUEST_JOINED, T0)
    at = START + timedelta(minutes=3)
    before, after = _step(guest, EventKind.HOST_JOINED, at)

    (fact,) = call_facts(EventKind.HOST_JOINED, _b(BookingKind.DIRECT), before, after, at)

    assert (fact.kind, fact.value_seconds) == (FactKind.CALL_HOST_JOINED, None)


def test_a_repeated_host_join_writes_nothing() -> None:
    host = apply_event(new_status(1), EventKind.HOST_JOINED, START)

    assert call_facts(EventKind.HOST_JOINED, _b(), host, host, START) == []


def test_the_end_of_a_meeting_carries_its_length_and_the_most_people_seen() -> None:
    status = new_status(1)
    for kind, at in (
        (EventKind.MEETING_STARTED, START),
        (EventKind.HOST_JOINED, START),
        (EventKind.GUEST_JOINED, START + timedelta(minutes=1)),
        (EventKind.GUEST_JOINED, START + timedelta(minutes=2)),
        (EventKind.PARTICIPANT_LEFT, START + timedelta(minutes=10)),
    ):
        status = apply_event(status, kind, at)
    end = START + timedelta(minutes=25)
    before, after = _step(status, EventKind.MEETING_ENDED, end)

    (fact,) = call_facts(EventKind.MEETING_ENDED, _b(), before, after, end)
    again = call_facts(EventKind.MEETING_ENDED, _b(), after, after, end)

    assert (fact.kind, fact.value_seconds, fact.participants) == (
        FactKind.CALL_ENDED,
        25 * 60,
        3,
    )
    assert after.participants == 0
    assert after.peak_participants == 3
    assert again == []


def test_a_meeting_that_never_started_has_no_length() -> None:
    before, after = _step(new_status(1), EventKind.MEETING_ENDED, START)

    (fact,) = call_facts(EventKind.MEETING_ENDED, _b(), before, after, START)

    assert fact.value_seconds is None
    assert fact.participants == 0


@pytest.mark.parametrize(
    ("detail", "reason"), [("waiting", "waiting"), ("agent", "agent"), ("x", None)]
)
def test_leaving_a_direct_call_carries_the_wait_and_when(detail: str, reason: str | None) -> None:
    direct = _b(BookingKind.DIRECT)
    at = START + timedelta(seconds=75)
    before, after = _step(new_status(1), EventKind.WAITING_LEFT, at)

    (fact,) = call_facts(EventKind.WAITING_LEFT, direct, before, after, at, detail)

    assert (fact.kind, fact.reason_code, fact.value_seconds) == (
        FactKind.CALL_WAITING_LEFT,
        reason,
        75,
    )


@pytest.mark.parametrize(
    ("detail", "code"),
    [
        ("rejected_400", "rejected"),
        ("polled:unauthorized", "unauthorized"),
        ("rate_limited", "rate_limited"),
        ("ENTRY_POINT_NOT_FOUND", "other"),
        ("Dear Ada Lovelace, ada@example.org", "other"),
        ("", "other"),
    ],
)
def test_a_failure_is_reduced_to_a_code_of_the_closed_list(detail: str, code: str) -> None:
    assert failure_code(detail) == code in FAILURE_CODES


@pytest.mark.parametrize(
    ("kind", "part"),
    [
        (EventKind.TASK_FAILED, "task"),
        (EventKind.MEETING_FAILED, "meeting"),
        (EventKind.INTEGRATION_ERROR, "integration"),
    ],
)
def test_a_failure_fact_names_the_part_and_the_code_only(kind: EventKind, part: str) -> None:
    before, after = _step(new_status(1), kind, T0)

    (fact,) = call_facts(kind, _b(), before, after, T0, "rejected_403 ada@example.org")

    assert fact.kind is FactKind.CALL_FAILED
    assert fact.reason_code == f"{part}.rejected"
    assert fact.actor_class is ActorClass.SYSTEM


def test_a_task_that_failed_already_is_not_counted_twice() -> None:
    failed = apply_event(new_status(1), EventKind.TASK_FAILED, T0)

    assert call_facts(EventKind.TASK_FAILED, _b(), failed, failed, T0, "x") == []


@pytest.mark.parametrize(
    ("event", "fact_kind"),
    [
        (EventKind.TRIAL_CREATED, FactKind.TRIAL_CREATED),
        (EventKind.TRIAL_JOINED, FactKind.TRIAL_JOINED),
        (EventKind.TRIAL_ENTERED, FactKind.TRIAL_ENTERED),
        (EventKind.TRIAL_FAILED, FactKind.TRIAL_FAILED),
    ],
)
def test_trial_events_make_trial_facts(event: EventKind, fact_kind: FactKind) -> None:
    status = new_status(1)

    (fact,) = call_facts(event, _b(), status, status, T0)

    assert fact.kind is fact_kind


def test_a_trial_step_counts_once_per_booking_but_a_failure_every_time() -> None:
    status = new_status(1)
    for event in FIRST_ONLY:
        assert call_facts(event, _b(), status, status, T0, first_of_kind=False) == []
    assert (
        len(call_facts(EventKind.TRIAL_FAILED, _b(), status, status, T0, first_of_kind=False)) == 1
    )


def test_a_host_who_entered_the_trial_is_no_guest() -> None:
    status = new_status(1)

    assert call_facts(EventKind.TRIAL_ENTERED, _b(), status, status, T0, "host") == []


def test_an_event_that_makes_no_fact_is_not_in_the_produces_set() -> None:
    status = new_status(1)
    for kind in set(EventKind) - PRODUCES:
        assert call_facts(kind, _b(), status, status, T0) == [], kind
