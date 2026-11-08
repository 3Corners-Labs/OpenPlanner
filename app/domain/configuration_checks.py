"""Checks the kinds of the version-1 format share (ADR 0099 point 7).

A document may do nothing the portal forbids, so a kind's rules call the functions the admin
screens call. A few checks serve more than one kind (a contact block sits on an organisation,
a group and a closure scenario; a setting's value is read the same for the platform and for
an organisation), and live here once (ADR 0005).
"""

from typing import cast

from app.domain.configuration_changes import RuleContext
from app.domain.configuration_values import Json, Value
from app.domain.contacts import block_from_json, block_to_json
from app.domain.errors import DomainError, ErrorCode
from app.domain.platform_settings import usable
from app.domain.settings_registry import Kind, SettingSpec
from app.domain.settings_service import parse

_PLAIN_KINDS: dict[Kind, tuple[type, ...]] = {
    Kind.INT: (int,),
    Kind.FLOAT: (int, float),
    Kind.BOOL: (bool,),
}
"""The JSON types a setting of a numeric or boolean kind may be written as; any other kind is
written as a text."""


def contacts_check(value: Value, context: RuleContext) -> Value:
    """Check a contact block as the portal does and return it as the portal stores it.

    A block without options is no block: the portal deletes the row and the parent's block
    applies, so it is returned as ``None``.

    Raises:
        DomainError: ``invalid_configuration`` naming ``contacts``, ``contacts_heading`` or
            the row and part (``contact_2_url``).
    """
    block = block_from_json(value)
    return None if block.is_empty else cast("Json", block_to_json(block))


def setting_value(spec: SettingSpec, value: Value, *, production: bool = False) -> Value:
    """Read the value a document gives a setting and return it as the registry stores it.

    The JSON type must be the kind's own (``5``, not ``"5"`` or ``true``); the value then goes
    through the parsing, type and bounds checks of the portal. With ``production`` the values
    a production installation never uses (ADR 0067) are refused as well.

    Raises:
        DomainError: ``setting_invalid`` with the key as field, never the value.
    """
    if spec.secret or not _typed_as_json(spec, value):
        raise DomainError(ErrorCode.SETTING_INVALID, spec.key)
    parsed = parse(spec, _as_text(value))
    if production and not usable(spec, parsed, production=True):
        raise DomainError(ErrorCode.SETTING_INVALID, spec.key)
    return parsed  # type: ignore[return-value]


def _typed_as_json(spec: SettingSpec, value: Value) -> bool:
    """Whether ``value`` is written as the JSON type the setting's kind has."""
    if isinstance(value, bool):
        return spec.kind is Kind.BOOL
    allowed = _PLAIN_KINDS.get(spec.kind, (str,))
    return isinstance(value, allowed)


def _as_text(value: Value) -> str:
    """Write a JSON scalar as the text the portal's parser reads."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
