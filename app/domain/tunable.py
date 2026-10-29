"""A setting a service reads each time it needs it, so a change in the portal applies live.

Services built once at start-up take a value or a function that returns it (ADR 0042 §6):
the composition root passes ``lambda: app.state.settings.x``, tests a plain value.
"""

from collections.abc import Callable


def value_of[T](value: T | Callable[[], T]) -> T:
    """Return ``value``, calling it first when it is a function."""
    return value() if callable(value) else value
