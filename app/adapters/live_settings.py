"""The settings web and worker run with, kept current with the portal (ADR 0042 §2, §6).

``LiveSettings`` holds the ``Settings`` read from the environment at start-up (the base) and
the ``PlatformSettingsStore`` with the stored portal values. ``refresh`` reloads the store when
``settings_version`` moved, rebuilds the effective ``Settings`` and hands them to ``apply``
(the web process puts them on ``app.state.settings``; the worker uses them in its next loop).
The web process refreshes every ``RELOAD_SECONDS`` in ``keep_current``; the worker once per
loop.

A value with an expiry (``LOG_LEVEL``, ADR 0042 §6) lapses without a new version: ``refresh``
also rebuilds the settings once the next expiry has passed.

``locked`` and ``combination_problem`` answer the questions ``SettingsService`` asks before it
stores a change: which keys the environment locks, and whether proposed portal values break a
cross-field rule of ``Settings``.

A failed read (the database is being restored, or not migrated yet) keeps the last values and
is logged by its exception type only.
"""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime

import structlog

from app.adapters.settings_report import (
    SettingState,
    combination_problem,
    combination_refused,
    describe,
    effective,
    locked_keys,
)
from app.config import Settings
from app.domain.platform_settings import (
    RELOAD_SECONDS,
    PlatformSettingsStore,
    PortalValues,
    resolve_for_organisation,
    usable,
)
from app.domain.ports import Clock
from app.domain.settings_registry import REGISTRY, SettingClass

log = structlog.get_logger()

ORGANISATION_SPECS = tuple(s for s in REGISTRY if s.class_ is SettingClass.ORGANISATION)
"""The class D settings, in registry order (ADR 0042 step 5)."""


class LiveSettings:
    """The effective settings of one process.

    Args:
        base: The settings read from the environment; never replaced.
        environ: The process environment, to tell an environment lock from a default.
        store: The stored portal values.
        clock: Current time, for values that lapse.
        apply: Called with the new effective settings after every change.
    """

    def __init__(
        self,
        base: Settings,
        environ: Mapping[str, str],
        store: PlatformSettingsStore,
        clock: Clock,
        apply: Callable[[Settings], None] = lambda _: None,
    ) -> None:
        """Start from the base; the portal values follow with the first ``refresh``."""
        self._base = base
        self._environ = dict(environ)
        self._store = store
        self._clock = clock
        self._apply = apply
        self._lapse: datetime | None = None
        self._by_organisation: dict[int, Settings] = {}
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
        now = self._clock.now()
        lapsed = self._lapse is not None and self._lapse <= now
        if changed or lapsed:
            portal = self._store.current
            self.settings = effective(self._base, self._environ, portal, now)
            self.refused = combination_refused(self._base, self._environ, portal, now)
            if self.refused:
                log.warning("settings.portal_values_refused", version=portal.version)
            self._lapse = portal.next_expiry(now)
            self._by_organisation.clear()
            self._apply(self.settings)
        return changed or lapsed

    def states(self) -> list[SettingState]:
        """Return every setting with its effective value and source, for the screens."""
        return describe(self._base, self._environ, self._store.current, self._clock.now())

    @property
    def portal(self) -> PortalValues:
        """The stored portal values as last read."""
        return self._store.current

    def organisation_id(self, slug: str | None) -> int | None:
        """Return the id of the organisation with ``slug`` (``None`` when unknown)."""
        return self._store.current.organisation_ids.get(slug) if slug else None

    def for_organisation(self, organisation_id: int | None) -> Settings:
        """Return the settings of one organisation: its class D values over the platform's.

        Values that break a cross-field rule together do not apply; the platform's stay.
        """
        own = self._store.current.organisations.get(organisation_id or 0)
        if not own:
            return self.settings
        key = organisation_id or 0
        if key not in self._by_organisation:
            update = {
                spec.key: own[spec.key]
                for spec in ORGANISATION_SPECS
                if spec.key in own and usable(spec, own[spec.key])
            }
            combined = self.settings.model_copy(update=update)
            self._by_organisation[key] = (
                self.settings if combined.cross_field_problem() else combined
            )
        return self._by_organisation[key]

    def organisation_combination_problem(
        self, organisation_id: int, proposed: Mapping[str, object]
    ) -> str | None:
        """Return the cross-field rule one organisation's proposed values would break, if any."""
        update = {
            spec.key: proposed[spec.key]
            for spec in ORGANISATION_SPECS
            if spec.key in proposed and usable(spec, proposed[spec.key])
        }
        return self.settings.model_copy(update=update).cross_field_problem()

    def organisation_states(self, organisation_id: int) -> list[SettingState]:
        """Return the class D settings of one organisation with value and source."""
        platform = {s.spec.key: s for s in self.states() if s.spec in ORGANISATION_SPECS}
        portal = self._store.current
        found = []
        for spec in ORGANISATION_SPECS:
            base = platform[spec.key]
            resolved = resolve_for_organisation(
                spec, base.value, base.source, portal, organisation_id
            )
            found.append(
                replace(
                    base,
                    source=resolved.source,
                    locked=False,
                    shown=_shown(resolved.value),
                    portal_ignored=resolved.portal_ignored,
                    value=resolved.value,
                    outside_bounds=False,
                )
            )
        return found

    def locked(self) -> frozenset[str]:
        """Return the keys the environment locks; the portal and the CLI never change them."""
        return locked_keys(self._environ)

    def combination_problem(self, proposed: Mapping[str, object]) -> str | None:
        """Return the cross-field rule that ``proposed`` portal values would break, if any."""
        return combination_problem(self._base, self._environ, proposed)


async def keep_current(live: LiveSettings, interval: float = RELOAD_SECONDS) -> None:
    """Refresh ``live`` every ``interval`` seconds until cancelled (the web process, §6)."""
    while True:
        await asyncio.sleep(interval)
        await live.refresh()


def _shown(value: object) -> str:
    """Write a class D value for the screens (never a secret)."""
    if value is None:
        return "(empty)"
    return str(value)
