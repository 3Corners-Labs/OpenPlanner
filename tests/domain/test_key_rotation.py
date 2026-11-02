"""Counting what a key rotation still has to move (ADR 0094 points 3 and 4)."""

from app.domain.key_rotation import (
    BookingCredential,
    RotationStatus,
    bookings_on_previous_key,
)
from app.domain.tokens import derive_token, hash_token

OLD = b"o" * 32
NEW = b"n" * 32


def _credential(key: bytes, salt: str) -> BookingCredential:
    return BookingCredential(salt, hash_token(derive_token(key, salt)))


def test_only_bookings_whose_link_was_made_under_the_previous_key_count() -> None:
    credentials = [
        _credential(OLD, "a"),
        _credential(OLD, "b"),
        _credential(NEW, "c"),
        BookingCredential("", "0" * 64),  # a booking from before salts: matches no key
        BookingCredential("d", "f" * 64),  # matches neither key
    ]

    assert bookings_on_previous_key(credentials, NEW, OLD) == 2


def test_no_bookings_is_zero() -> None:
    assert bookings_on_previous_key([], NEW, OLD) == 0


def test_a_counter_at_zero_says_the_previous_key_may_go() -> None:
    status = RotationStatus(records=0, bookings=3)

    assert status.records_done
    assert not status.bookings_done


def test_without_a_previous_key_there_is_nothing_to_say() -> None:
    status = RotationStatus(records=None, bookings=None)

    assert not status.records_done
    assert not status.bookings_done
    assert not status.active
    assert RotationStatus(records=1, bookings=None).active
