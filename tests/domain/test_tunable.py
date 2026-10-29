"""A value or a function returning it (ADR 0042 §6)."""

from datetime import timedelta

from app.domain.tunable import value_of


def test_a_plain_value_is_returned_as_it_is() -> None:
    assert value_of(timedelta(minutes=15)) == timedelta(minutes=15)


def test_a_function_is_called_each_time() -> None:
    current = {"minutes": 15}

    def window() -> timedelta:
        return timedelta(minutes=current["minutes"])

    assert value_of(window) == timedelta(minutes=15)
    current["minutes"] = 5
    assert value_of(window) == timedelta(minutes=5)
