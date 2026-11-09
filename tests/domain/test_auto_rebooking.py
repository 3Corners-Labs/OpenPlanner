"""The rules of automatic proposals (ADR 0057 point 6): wait, reset, forget, ceiling."""

from datetime import UTC, datetime, timedelta

from app.domain.auto_rebooking import (
    Seen,
    ShortfallWatch,
    fingerprint_of,
    needs_review,
    scan,
)

NOW = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
SLOT = datetime(2026, 1, 7, 9, 0, tzinfo=UTC)
DELAY = timedelta(minutes=15)


def watch(fingerprint: str = "1/3", seen_ago: timedelta = timedelta(0)) -> ShortfallWatch:
    return ShortfallWatch(1, SLOT, fingerprint, NOW - seen_ago)


def test_a_new_shortfall_starts_waiting_and_is_not_due() -> None:
    result = scan(1, [Seen(SLOT, "1/3")], {}, NOW, DELAY)

    assert result.store == (ShortfallWatch(1, SLOT, "1/3", NOW),)
    assert result.due == ()
    assert result.gone == ()


def test_the_same_shortfall_is_due_once_the_delay_has_passed() -> None:
    known = {SLOT: watch(seen_ago=DELAY)}

    result = scan(1, [Seen(SLOT, "1/3")], known, NOW, DELAY)

    assert result.due == (known[SLOT],)
    assert result.store == ()


def test_the_same_shortfall_is_not_due_before_the_delay() -> None:
    known = {SLOT: watch(seen_ago=DELAY - timedelta(minutes=1))}

    assert scan(1, [Seen(SLOT, "1/3")], known, NOW, DELAY).due == ()


def test_a_shortfall_that_changed_waits_again_from_now() -> None:
    """A typo corrected and corrected again: the second state must hold for the whole delay."""
    known = {SLOT: watch("1/3", seen_ago=timedelta(hours=2))}

    result = scan(1, [Seen(SLOT, "2/3")], known, NOW, DELAY)

    assert result.store == (ShortfallWatch(1, SLOT, "2/3", NOW),)
    assert result.due == ()


def test_a_slot_that_is_no_longer_short_is_forgotten() -> None:
    known = {SLOT: watch(seen_ago=timedelta(hours=1))}

    result = scan(1, [], known, NOW, DELAY)

    assert result.gone == (SLOT,)
    assert result.due == ()


def test_the_fingerprint_names_agents_and_places_taken() -> None:
    assert fingerprint_of(1, 3) == "1/3"
    assert fingerprint_of(3, 1) != fingerprint_of(1, 3)


def test_a_review_is_needed_only_above_the_ceiling() -> None:
    assert not needs_review(10, 10)
    assert needs_review(11, 10)


def test_notifying_keeps_the_rest_of_the_watch() -> None:
    notified = watch(seen_ago=DELAY).notify(NOW)

    assert (notified.notified_at, notified.fingerprint, notified.slot_start) == (NOW, "1/3", SLOT)
