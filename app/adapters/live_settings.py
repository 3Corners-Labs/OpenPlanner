"""The settings web and worker run with, kept current with the portal (ADR 0042 §2, §6).

``LiveSettings`` holds the ``Settings`` read from the environment at start-up (the base) and
the ``PlatformSettingsStore`` with the stored portal values. ``refresh`` reloads the store when
``settings_version`` moved, rebuilds the effective ``Settings`` and hands them to ``apply``
(the web process puts them on ``app.state.settings``; the worker uses them in its next loop).
The web process refreshes every ``RELOAD_SECONDS`` in ``keep_current``; the worker once per
loop.

A failed read (the database is being restored, or not migrated yet) keeps the last values and
is logged by its exception type only.
"""

import asyncio
from collections.abc import Callable, Mapping

import structlog

from app.adapters.settings_report import SettingState, combination_refused, describe, effective
from app.config import Settings
from app.domain.platform_settings import RELOAD_SECONDS, PlatformSettingsStore

log = structlog.get_logger()


class LiveSettings:
    """The effective settings of one process.

    Args:
        base: The settings read from the environment; never replaced.
        environ: The process environment, to tell an environment lock from a default.
        store: The stored portal values.
        apply: Called with the new effective settings after every change.
    """

    def __init__(
        self,
        base: Settings,
        environ: Mapping[str, str],
        store: PlatformSettingsStore,
        apply: Callable[[Settings], None] = lambda _: None,
    ) -> None:
        """Start from the base; the portal values follow with the first ``refresh``."""
        self._base = base
        self._environ = dict(environ)
        self._store = store
        self._apply = apply
        self.settings = base
        self.refused = False
        """Whether the stored values together break a rule, so none of them applies."""

    async def refresh(self) -> bool:
        """Reload the portal values if they changed and apply them; return whether they did."""
        try:
            changed = await self._store.refresh()
        except Exception as exc:  # the database may be away; the last values stay
            log.warning("settings.reload_failed", error=type(exc).__name__)
            return False
        if changed:
            portal = self._store.current
            self.settings = effective(self._base, self._environ, portal)
            self.refused = combination_refused(self._base, self._environ, portal)
            if self.refused:
                log.warning("settings.portal_values_refused", version=portal.version)
            self._apply(self.settings)
        return changed

    def states(self) -> list[SettingState]:
        """Return every setting with its effective value and source, for the screens."""
        return describe(self._base, self._environ, self._store.current)


async def keep_current(live: LiveSettings, interval: float = RELOAD_SECONDS) -> None:
    """Refresh ``live`` every ``interval`` seconds until cancelled (the web process, §6)."""
    while True:
        await asyncio.sleep(interval)
        await live.refresh()
