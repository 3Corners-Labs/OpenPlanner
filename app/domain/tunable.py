"""A setting a service reads each time it needs it, so a change in the portal applies live.

Services built once at start-up take a value or a function that returns it (ADR 0042 §6):
the composition root passes ``lambda: app.state.settings.x``, tests a plain value. A class D
setting differs per organisation (ADR 0042 step 5): the function then takes the
organisation id (``value_for``).
"""

from collections.abc import Callable


def value_of[T](value: T | Callable[[], T]) -> T:
    """Return ``value``, calling it first when it is a function."""
    return value() if callable(value) else value


def value_for[T](value: T | Callable[[int], T], organisation_id: int) -> T:
    """Return ``value`` for one organisation, calling it with the id when it is a function."""
    return value(organisation_id) if callable(value) else value
