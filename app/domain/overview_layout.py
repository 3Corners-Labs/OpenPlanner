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
    """Changes the layout of the admin who is signed in, one step at a time.

    Each change reads the stored layout (or the role's default), applies one rule of this
    module and stores the result in the same transaction, so two tabs cannot overwrite each
    other's step. The maximum applies only when a widget is added: a layout above a lowered
    maximum can still be shortened or reordered.

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
        """Return ``subject``'s own layout, or ``None`` when the role's default applies."""
        async with self._uow.read() as tx:
            return await tx.get_overview_layout(subject)

    async def layout(self, subject: str, default: Layout, allowed: Sequence[str]) -> Layout:
        """Return what ``subject``'s overview shows (``effective``)."""
        async with self._uow.read() as tx:
            return effective(await tx.get_overview_layout(subject), default, allowed)

    async def add(self, subject: str, key: str, default: Layout, allowed: Sequence[str]) -> None:
        """Add ``key`` at the end of ``subject``'s layout (``add``).

        Raises:
            DomainError: as ``add``, or ``invalid_configuration`` on ``staff``.
        """
        await self._change(
            subject, default, allowed, lambda c: add(c, key, allowed, self._maximum())
        )

    async def remove(self, subject: str, key: str, default: Layout, allowed: Sequence[str]) -> None:
        """Remove ``key`` from ``subject``'s layout (``remove``)."""
        await self._change(subject, default, allowed, lambda c: remove(c, key))

    async def move(
        self, subject: str, key: str, offset: int, default: Layout, allowed: Sequence[str]
    ) -> None:
        """Move ``key`` one place up or down in ``subject``'s layout (``move``)."""
        await self._change(subject, default, allowed, lambda c: move(c, key, offset))

    async def reset(self, subject: str) -> None:
        """Delete ``subject``'s own layout, so the role's default applies again (§4)."""
        async with self._uow.locked() as tx:
            await tx.save_overview_layout(subject, None, self._clock.now())

    async def _change(
        self,
        subject: str,
        default: Layout,
        allowed: Sequence[str],
        step: Callable[[Layout], Layout],
    ) -> None:
        """Apply ``step`` to the shown layout and store the result as ``subject``'s own.

        Raises:
            DomainError: ``invalid_configuration`` on ``widget`` for a key outside the
                register, on ``staff`` when ``subject`` has no staff record.
        """
        async with self._uow.locked() as tx:
            current = effective(await tx.get_overview_layout(subject), default, allowed)
            changed = step(current)
            if not set(changed) <= self._known:
                raise _refuse("widget")
            if not await tx.save_overview_layout(subject, changed, self._clock.now()):
                raise _refuse("staff")
