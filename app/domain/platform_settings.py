"""Portal values of the platform settings, the environment lock and live reloading (ADR 0042).

A class C or D setting is resolved in this order (§2)::

    code default ──► portal value (if stored and within the rules) ──► environment lock

The environment wins whenever its variable is set; the portal then shows the setting
read-only. A stored value that breaks the registry's rules (a hand-edited row, a bound that
became stricter) is ignored rather than applied, and reported as such.

``PlatformSettingsStore`` keeps the stored values in memory. Each ``refresh`` reads
``settings_version`` and reads the values again only when the counter moved (§6). The web
process refreshes every ``RELOAD_SECONDS``, the worker once per loop.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.ports import UnitOfWork
from app.domain.settings_registry import Kind, SettingSpec, within_bounds

RELOAD_SECONDS = 10
"""How stale the web process may be after a change: ADR 0042 §6 sets at most 10 seconds."""


class Source(StrEnum):
    """Where a setting's effective value comes from."""

    DEFAULT = "default"
    PORTAL = "portal"
    ENV = "env"
    FILE = "file"


@dataclass(frozen=True)
class PortalValues:
    """The stored portal values with the ``settings_version`` they were read at.

    Attributes:
        version: The counter at the time of reading; 0 means nothing was ever changed.
        values: Plain JSON values by setting key; secrets are never among them.
    """

    version: int
    values: Mapping[str, object] = field(default_factory=dict)


EMPTY = PortalValues(0)
"""No stored values: what a fresh installation, or one that never read them, has."""


@dataclass(frozen=True)
class Resolved:
    """One setting's effective value and where it came from.

    Attributes:
        spec: The registry entry.
        value: The effective value.
        source: Default, portal, environment or ``X_FILE``.
        locked: Whether the environment locks a portal setting (class C or D).
        outside_bounds: Whether an environment value lies outside the portal bounds; it is
            applied, but flagged (product owner, 2026-10-28).
        portal_ignored: Whether a stored portal value exists but breaks the rules.
    """

    spec: SettingSpec
    value: object
    source: Source
    locked: bool
    outside_bounds: bool = False
    portal_ignored: bool = False


_TYPES: dict[Kind, tuple[type, ...]] = {
    Kind.INT: (int,),
    Kind.FLOAT: (int, float),
    Kind.BOOL: (bool,),
    Kind.ENUM: (str,),
    Kind.STRING: (str,),
    Kind.URL: (str,),
    Kind.RATE: (str,),
}


def usable(spec: SettingSpec, value: object) -> bool:
    """Return whether ``value`` may be applied as the portal value of ``spec``.

    Only class C and D settings that are not secrets, with a value of the right type within
    the portal bounds. Secrets are stored sealed elsewhere (ADR 0008), never here.
    """
    if not spec.in_portal or spec.secret:
        return False
    if value is None:
        return within_bounds(spec, None)
    types = _TYPES.get(spec.kind, ())
    if isinstance(value, bool) and spec.kind is not Kind.BOOL:
        return False
    return isinstance(value, types) and within_bounds(spec, value)


def resolve(
    spec: SettingSpec, env_value: object, env_source: Source | None, portal: PortalValues
) -> Resolved:
    """Resolve one setting: code default, then portal value, then environment lock.

    Args:
        spec: The registry entry.
        env_value: What the environment gave the process: the variable's value, or the
            code default when it is not set.
        env_source: ``ENV`` or ``FILE`` when a variable sets it, else ``None``.
        portal: The stored portal values.
    """
    stored = spec.key in portal.values
    ignored = stored and spec.in_portal and not usable(spec, portal.values[spec.key])
    if env_source is not None:
        outside = spec.in_portal and not spec.secret and not within_bounds(spec, env_value)
        return Resolved(spec, env_value, env_source, spec.lockable, outside, ignored)
    if stored and not ignored and spec.in_portal:
        return Resolved(spec, portal.values[spec.key], Source.PORTAL, False)
    return Resolved(spec, env_value, Source.DEFAULT, False, portal_ignored=ignored)


class PlatformSettingsStore:
    """Keeps the stored portal values in memory and reloads them when they change (§6).

    Args:
        uow: Opens database transactions.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        """Store the unit of work; nothing is read until the first ``refresh``."""
        self._uow = uow
        self._loaded = False
        self.current = EMPTY

    async def refresh(self) -> bool:
        """Read the stored values again if ``settings_version`` moved; return whether it did.

        The first call always loads.
        """
        async with self._uow.read() as tx:
            version = await tx.settings_version()
            if self._loaded and version == self.current.version:
                return False
            values = await tx.platform_settings()
        self.current = PortalValues(version, values)
        self._loaded = True
        return True
