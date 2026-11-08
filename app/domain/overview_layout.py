"""An admin's own overview layout (ADR 0096): which widgets, in which order.

A layout is an ordered list of widget keys kept on the staff member. Without one, the default
layout of the admin's role applies (ADR 0061). It is a personal preference, not a setting: it
holds keys only (no personal data), is never shared and needs no permission of its own beyond
opening the overview. The widget catalogue and the matrix live outside the domain (the widget
register in ``app/web/widgets.py`` and ``app/domain/authorization.py``); the functions here take
the keys the reader may see as an argument, so a key whose right was lost is skipped silently
and never offered (ADR 0096 §3).
"""

from collections.abc import Callable, Sequence

from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, UnitOfWork

Layout = tuple[str, ...]
"""Widget keys in the order the overview shows them."""


def effective(stored: Layout | None, default: Layout, allowed: Sequence[str]) -> Layout:
    """Return the widgets the overview shows: the own layout, else the role's default.

    Keys the reader may not see (a right lost, a widget removed from the code) are skipped.
    """
    return tuple(key for key in (default if stored is None else stored) if key in allowed)


def addable(current: Layout, allowed: Sequence[str]) -> Layout:
    """Return the widgets the reader may add: allowed and not shown yet, in catalogue order."""
    return tuple(key for key in allowed if key not in current)


def _refuse(field_name: str) -> DomainError:
    """Return the refusal of a layout change, naming what was wrong."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field=field_name)


def add(current: Layout, key: str, allowed: Sequence[str], maximum: int) -> Layout:
    """Return ``current`` with ``key`` at the end.

    Raises:
        DomainError: ``invalid_configuration`` on ``widget`` when ``key`` is not offered (not
            allowed or already shown), on ``widget_count`` when the layout is full.
    """
    if key not in addable(current, allowed):
        raise _refuse("widget")
    if len(current) >= maximum:
        raise _refuse("widget_count")
    return (*current, key)


def remove(current: Layout, key: str) -> Layout:
    """Return ``current`` without ``key``.

    Raises:
        DomainError: ``invalid_configuration`` on ``widget`` when ``key`` is not shown.
    """
    if key not in current:
        raise _refuse("widget")
    return tuple(k for k in current if k != key)


def move(current: Layout, key: str, offset: int) -> Layout:
    """Return ``current`` with ``key`` one place up (``offset=-1``) or down (``+1``).

    Moving the first widget up or the last one down changes nothing.

    Raises:
        DomainError: ``invalid_configuration`` on ``widget`` when ``key`` is not shown, on
            ``offset`` for anything but -1 or +1.
    """
    if key not in current:
        raise _refuse("widget")
    if offset not in (-1, 1):
        raise _refuse("offset")
    keys = list(current)
    index = keys.index(key)
    target = index + offset
    if 0 <= target < len(keys):
        keys[index], keys[target] = keys[target], keys[index]
    return tuple(keys)


class OverviewLayoutService:
    """Reads, saves and resets the layout of the admin who is signed in.

    Args:
        uow: Opens database transactions.
        clock: Current time, for the moment a layout last changed.
        known: The widget keys that exist (the register), so nothing else is ever stored.
        maximum: The most widgets a layout may hold (``overview_max_widgets``, read live).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        known: Sequence[str],
        maximum: Callable[[], int],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._known = frozenset(known)
        self._maximum = maximum

    @property
    def maximum(self) -> int:
        """The most widgets a layout may hold now."""
        return self._maximum()

    async def stored(self, subject: str) -> Layout | None:
        """Return the own layout of ``subject``, or ``None`` when the role's default applies."""
        async with self._uow.read() as tx:
            return await tx.get_overview_layout(subject)

    async def save(self, subject: str, layout: Sequence[str]) -> None:
        """Store ``layout`` as ``subject``'s own.

        Raises:
            DomainError: ``invalid_configuration`` on ``widget`` for an unknown or repeated
                key, on ``widget_count`` above the maximum, on ``staff`` when ``subject`` has
                no staff record (a person who never logged in).
        """
        keys = tuple(layout)
        if len(set(keys)) != len(keys) or not set(keys) <= self._known:
            raise _refuse("widget")
        if len(keys) > self._maximum():
            raise _refuse("widget_count")
        async with self._uow.locked() as tx:
            if not await tx.save_overview_layout(subject, keys, self._clock.now()):
                raise _refuse("staff")

    async def reset(self, subject: str) -> None:
        """Delete ``subject``'s own layout, so the role's default applies again (§4)."""
        async with self._uow.locked() as tx:
            await tx.save_overview_layout(subject, None, self._clock.now())
