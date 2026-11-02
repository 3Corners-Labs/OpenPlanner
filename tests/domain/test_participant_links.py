"""The rules of the participant link session (ADR 0093)."""

from datetime import timedelta

from app.domain.participant_links import (
    MAX_BOOKINGS,
    TOUCH_EVERY,
    LinkSession,
    PublicRef,
    hash_value,
    new_public_ref,
    new_value,
    overflow,
)
from tests.support.domain import NOW


def _session(**changes: object) -> LinkSession:
    values: dict[str, object] = {
        "id": 1,
        "value_hash": "h",
        "booking_ids": (1, 2),
        "last_used_at": NOW,
        "expires_at": NOW + timedelta(hours=24),
    }
    return LinkSession(**{**values, **changes})  # type: ignore[arg-type]


def test_values_and_references_are_random_and_long_enough() -> None:
    assert new_value() != new_value()
    assert new_public_ref() != new_public_ref()
    assert len(new_value()) >= 43  # 32 bytes
    assert len(new_public_ref()) >= 22  # 16 bytes


def test_only_the_hash_of_a_value_is_compared() -> None:
    value = new_value()

    assert hash_value(value) == hash_value(value)
    assert value not in hash_value(value)
    assert len(hash_value(value)) == 64


def test_a_session_lapses_at_its_end() -> None:
    session = _session()

    assert session.alive(NOW + timedelta(hours=23, minutes=59))
    assert not session.alive(NOW + timedelta(hours=24))


def test_a_busy_session_writes_its_extension_at_most_once_a_minute() -> None:
    session = _session()

    assert not session.due_for_touch(NOW + TOUCH_EVERY - timedelta(seconds=1))
    assert session.due_for_touch(NOW + TOUCH_EVERY)


def test_a_session_holds_only_its_own_bookings() -> None:
    session = _session()

    assert session.holds(1)
    assert not session.holds(3)


def test_over_twenty_bookings_the_oldest_drop_out() -> None:
    ids = tuple(range(1, MAX_BOOKINGS + 3))

    assert overflow(ids) == (1, 2)
    assert overflow(ids[:MAX_BOOKINGS]) == ()
    assert overflow(()) == ()


def test_a_public_reference_is_a_plain_value() -> None:
    assert PublicRef("abc") == PublicRef("abc")
    assert PublicRef("abc").value == "abc"
