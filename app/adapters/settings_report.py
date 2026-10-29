"""Where each setting's value comes from, for the host CLI ``settings list/show`` (ADR 0042).

Reads the effective ``Settings`` and the process environment. Secrets are reported as ``set``
or ``not set``, never their value. Until the portal stores values (build step 3) the source is
``env``, ``file`` (``X_FILE``, ADR 0042 §9) or ``default``; a class C or D setting from the
environment is reported as locked.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import SecretStr

from app.config import Settings
from app.domain.settings_registry import REGISTRY, SettingSpec, bounds_text, within_bounds


@dataclass(frozen=True)
class SettingState:
    """One setting as the operator sees it on the host.

    Attributes:
        spec: The registry entry.
        source: ``env`` when an environment variable sets it, ``file`` when ``X_FILE`` does,
            else ``default``.
        locked: Whether the environment locks a portal setting (class C or D).
        shown: The value to print: ``set``/``not set`` for a secret.
        outside_bounds: Whether an environment value lies outside the portal bounds; accepted,
            but flagged (product owner, 2026-10-28).
    """

    spec: SettingSpec
    source: str
    locked: bool
    shown: str
    outside_bounds: bool


def describe(settings: Settings, environ: Mapping[str, str]) -> list[SettingState]:
    """Return the state of every registered setting, in registry order."""
    names = {name.lower() for name in environ}
    return [_state(spec, settings, environ, names) for spec in REGISTRY]


def _state(
    spec: SettingSpec, settings: Settings, environ: Mapping[str, str], names: set[str]
) -> SettingState:
    """Describe one setting."""
    from_file = f"{spec.key}_file" in names
    from_env = bool({spec.key, *spec.aliases} & names) or from_file
    source = "file" if from_file else "env" if from_env else "default"
    value = getattr(settings, spec.key) if spec.in_config else _env_value(spec, environ)
    if spec.secret:
        shown = "set" if value is not None else "not set"
    else:
        shown = _text(value)
    plain = None if isinstance(value, SecretStr) else _plain(value)
    outside = from_env and spec.in_portal and not spec.secret and not within_bounds(spec, plain)
    return SettingState(spec, source, from_env and spec.lockable, shown, outside)


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
    return "".join(f"{label + ':':<15}{value}\n" for label, value in rows)
