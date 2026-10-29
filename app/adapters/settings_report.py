"""Where each setting's value comes from, for the CLI and the settings screens (ADR 0042).

Combines the ``Settings`` read from the environment, the process environment itself and the
stored portal values (``app.domain.platform_settings``). The source is ``env``, ``file``
(``X_FILE``, §9), ``portal`` or ``default``; a class C or D setting from the environment is
reported as locked. Secrets are reported as ``set`` or ``not set``, never their value.

``effective`` builds the ``Settings`` the processes run with: the environment's, with every
unlocked, valid portal value applied on top.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import SecretStr

from app.config import Settings
from app.domain.platform_settings import EMPTY, PortalValues, Resolved, Source, resolve
from app.domain.settings_registry import REGISTRY, SettingSpec, bounds_text


@dataclass(frozen=True)
class SettingState:
    """One setting as the operator sees it on the host.

    Attributes:
        spec: The registry entry.
        source: ``env`` when an environment variable sets it, ``file`` when ``X_FILE`` does,
            ``portal`` for a stored portal value, else ``default``.
        locked: Whether the environment locks a portal setting (class C or D).
        shown: The value to print: ``set``/``not set`` for a secret.
        outside_bounds: Whether an environment value lies outside the portal bounds; accepted,
            but flagged (product owner, 2026-10-28).
        portal_ignored: Whether a stored portal value breaks the registry's rules and is
            therefore not applied.
        value: The plain effective value (``None`` for a secret, which is never shown).
    """

    spec: SettingSpec
    source: Source
    locked: bool
    shown: str
    outside_bounds: bool
    portal_ignored: bool = False
    value: object = None

    @property
    def is_set(self) -> bool:
        """Whether the setting has a value (for a secret: whether it is set at all)."""
        return self.shown not in ("not set", "(empty)")


def describe(
    settings: Settings, environ: Mapping[str, str], portal: PortalValues = EMPTY
) -> list[SettingState]:
    """Return the state of every registered setting, in registry order.

    Args:
        settings: What the environment gave the process (never already ``effective``).
        environ: The process environment, to tell a variable from a default.
        portal: The stored portal values.
    """
    names = {name.lower() for name in environ}
    return [_state(_resolve(spec, settings, environ, names, portal)) for spec in REGISTRY]


def effective(settings: Settings, environ: Mapping[str, str], portal: PortalValues) -> Settings:
    """Return ``settings`` with every unlocked, valid portal value applied (ADR 0042 §2).

    The values were validated against the registry's bounds by ``resolve``; ``settings``
    itself is left unchanged.
    """
    names = {name.lower() for name in environ}
    update = {
        spec.key: resolved.value
        for spec in REGISTRY
        if spec.in_config
        and (resolved := _resolve(spec, settings, environ, names, portal)).source is Source.PORTAL
    }
    return settings.model_copy(update=update) if update else settings


def _resolve(
    spec: SettingSpec,
    settings: Settings,
    environ: Mapping[str, str],
    names: set[str],
    portal: PortalValues,
) -> Resolved:
    """Resolve one setting from the environment and the portal."""
    from_file = f"{spec.key}_file" in names
    from_env = bool({spec.key, *spec.aliases} & names) or from_file
    source = Source.FILE if from_file else Source.ENV if from_env else None
    value = getattr(settings, spec.key) if spec.in_config else _env_value(spec, environ)
    plain = value if isinstance(value, SecretStr) else _plain(value)
    return resolve(spec, plain, source, portal)


def _state(resolved: Resolved) -> SettingState:
    """Describe one resolved setting without ever holding a secret's value."""
    spec, value = resolved.spec, resolved.value
    if spec.secret:
        shown, value = ("set" if value is not None else "not set"), None
    else:
        shown = _text(value)
    return SettingState(
        spec,
        resolved.source,
        resolved.locked,
        shown,
        resolved.outside_bounds,
        resolved.portal_ignored,
        value,
    )


def _env_value(spec: SettingSpec, environ: Mapping[str, str]) -> str | None:
    """Return a variable this process does not read itself (the proxy's), if present."""
    return next((v for k, v in environ.items() if k.lower() == spec.key), None)


def _plain(value: object) -> object:
    """Turn a ``Settings`` value into the registry's plain form."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _text(value: object) -> str:
    """Write a non-secret value for the terminal."""
    plain = _plain(value)
    if plain is None:
        return "(empty)"
    if isinstance(plain, bool):
        return "true" if plain else "false"
    return str(plain)


def format_list(states: Sequence[SettingState]) -> str:
    """Return one line per setting: variable, class, source, lock, value and a bounds flag."""
    width = max(len(s.spec.env) for s in states)
    lines = [f"{'VARIABLE':<{width}}  CLASS  SOURCE   LOCKED  VALUE"]
    for state in states:
        lock = "yes" if state.locked else ("-" if not state.spec.lockable else "no")
        flag = "  ! outside portal bounds" if state.outside_bounds else ""
        flag += "  ! stored portal value ignored" if state.portal_ignored else ""
        lines.append(
            f"{state.spec.env:<{width}}  {state.spec.class_.value:<5}  {state.source:<7}  "
            f"{lock:<6}  {state.shown}{flag}"
        )
    return "\n".join(lines) + "\n"


def format_one(state: SettingState) -> str:
    """Return every detail of one setting, one per line."""
    spec = state.spec
    rows = [
        ("variable", spec.env),
        ("class", f"{spec.class_.value} ({spec.class_.name.lower()})"),
        ("description", spec.description),
        ("kind", spec.kind.value),
        ("value", state.shown),
        ("source", state.source),
        ("locked", "yes" if state.locked else "no"),
        ("default", "(none)" if spec.secret else _text(spec.default)),
        ("portal bounds", bounds_text(spec) or "-"),
        ("permission", spec.permission or "- (environment only)"),
        ("applies", spec.apply.value),
        ("aliases", ", ".join(a.upper() for a in spec.aliases) or "-"),
    ]
    if state.outside_bounds:
        rows.append(("warning", "outside the portal bounds; accepted from the environment"))
    if state.portal_ignored:
        rows.append(("warning", "the stored portal value breaks the rules and is ignored"))
    return "".join(f"{label + ':':<15}{value}\n" for label, value in rows)
