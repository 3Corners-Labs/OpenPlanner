"""The organisation texts behind the translator: a small in-process copy of the table.

``app.i18n.Translator`` looks texts up synchronously while the database is asynchronous, so this
``OverrideSource`` keeps every organisation's texts in memory (a few dozen rows per organisation
at most: the allow-list of ``app.domain.text_overrides`` bounds it). ``refresh`` re-reads the
table at most every ``REFRESH_SECONDS`` (ADR 0004 §9); a process that saves a text calls
``invalidate`` so its own next request already shows the change, and the other process (web or
worker) converges within the interval.
"""

import time
from collections.abc import Callable

import structlog

from app.domain.ports import UnitOfWork

log = structlog.get_logger()

REFRESH_SECONDS = 5.0
"""How long a process may show a stale text (ADR 0004 §9, fixed: a cache bound, not a policy)."""


class CachedTextOverrides:
    """``OverrideSource`` over the ``organisation_texts`` table, cached per process.

    Args:
        uow: Opens the read transaction that loads the table; may be given later with
            ``attach`` (the web app builds its translator before the database is open).
        monotonic: Seconds on a clock that never goes back; replaced in tests.
    """

    def __init__(
        self, uow: UnitOfWork | None = None, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        """Start empty; the first ``refresh`` loads the table."""
        self._uow = uow
        self._monotonic = monotonic
        self._texts: dict[tuple[int, str, str], str] = {}
        self._loaded_at: float | None = None

    def get(self, locale: str, key: str, organisation_id: int | None) -> str | None:
        """Return the organisation's text of ``key`` in ``locale``, or ``None``.

        ``organisation_id=None`` asks for a global override, which is not built (ADR 0074).
        """
        if organisation_id is None:
            return None
        return self._texts.get((organisation_id, locale, key))

    def attach(self, uow: UnitOfWork) -> None:
        """Give the source its database; the next ``refresh`` loads the table."""
        self._uow = uow
        self.invalidate()

    def invalidate(self) -> None:
        """Make the next ``refresh`` read the table, whatever its age."""
        self._loaded_at = None

    async def refresh(self) -> None:
        """Re-read the table unless it was read less than ``REFRESH_SECONDS`` ago.

        A failed read keeps the last copy (the shipped texts at first) and is retried after the
        same interval, so pages keep rendering while the database is unwell.
        """
        if self._uow is None:
            return
        now = self._monotonic()
        if self._loaded_at is not None and now - self._loaded_at < REFRESH_SECONDS:
            return
        try:
            async with self._uow.read() as tx:
                self._texts = await tx.get_all_organisation_texts()
        except Exception as exc:  # a missing table or a busy database must not fail a page
            log.warning("i18n.overrides_refresh_failed", error=type(exc).__name__)
        self._loaded_at = now
