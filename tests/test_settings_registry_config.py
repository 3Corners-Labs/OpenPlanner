"""``app/config.py`` and the settings registry describe the same settings (ADR 0042 §3).

A new ``Settings`` field without a registry entry fails here: give it an entry with its class
(A–D), default and portal bounds in ``app/domain/settings_registry.py``, then regenerate
``docs/settings.md``.
"""

from enum import Enum
from pathlib import Path

import pytest
from pydantic import AliasChoices, SecretStr

from app.config import Settings
from app.domain.settings_registry import BY_KEY, CLOCK_STYLES, LOG_LEVELS, REGISTRY, Kind
from app.i18n.dates import ClockStyle

FIELDS = Settings.model_fields

_KINDS: dict[object, Kind] = {
    bool: Kind.BOOL,
    int: Kind.INT,
    float: Kind.FLOAT,
    Path: Kind.PATH,
    SecretStr | None: Kind.SECRET,
}


def _plain(value: object) -> object:
    """Normalise a ``Settings`` default to the registry's plain form."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def test_every_config_field_has_a_registry_entry() -> None:
    missing = sorted(set(FIELDS) - set(BY_KEY))

    assert missing == [], f"add these to app/domain/settings_registry.py: {missing}"


def test_every_registry_entry_read_by_the_app_is_a_config_field() -> None:
    stale = sorted(spec.key for spec in REGISTRY if spec.in_config and spec.key not in FIELDS)

    assert stale == []


@pytest.mark.parametrize("name", sorted(FIELDS))
def test_defaults_match(name: str) -> None:
    assert _plain(FIELDS[name].default) == BY_KEY[name].default


@pytest.mark.parametrize("name", sorted(FIELDS))
def test_aliases_match(name: str) -> None:
    alias = FIELDS[name].validation_alias
    choices = alias.choices if isinstance(alias, AliasChoices) else []

    assert tuple(c for c in choices if c != name) == BY_KEY[name].aliases


@pytest.mark.parametrize("name", sorted(FIELDS))
def test_kinds_match_the_annotation(name: str) -> None:
    annotation = FIELDS[name].annotation
    expected = _KINDS.get(annotation)

    if expected is not None:
        assert BY_KEY[name].kind is expected
    if BY_KEY[name].kind is Kind.SECRET:
        assert annotation == SecretStr | None


def test_enum_choices_match_the_code() -> None:
    assert CLOCK_STYLES == tuple(style.value for style in ClockStyle)
    assert set(LOG_LEVELS) == set(Settings.model_fields["log_level"].annotation.__args__)  # type: ignore[union-attr]
