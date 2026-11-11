"""Checks the kinds of the version-1 format share (ADR 0099 point 7).

A document may do nothing the portal forbids, so a kind's rules call the functions the admin
screens call. What several kinds need lives here once (ADR 0005): the small readers of a
field, the contact block, the value of a setting, the entry checks of settings and texts, the
pair of agent-term words and the cross-field rule of the settings.
"""

from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from datetime import UTC, datetime
from typing import cast

from app.domain.agent_term import TERM_KEYS, check_term_pair
from app.domain.configuration_changes import Refusal, Resolution, RuleContext
from app.domain.configuration_schema import KindCheck
from app.domain.configuration_tree import Entity
from app.domain.configuration_values import Json, Value
from app.domain.contacts import block_from_json, block_to_json
from app.domain.errors import DomainError, ErrorCode
from app.domain.platform_settings import usable
from app.domain.settings_registry import BY_KEY, Kind, SettingSpec
from app.domain.settings_service import parse

_PLAIN_KINDS: dict[Kind, tuple[type, ...]] = {
    Kind.INT: (int,),
    Kind.FLOAT: (int, float),
    Kind.BOOL: (bool,),
}
"""The JSON types a setting of a numeric or boolean kind may be written as; any other kind is
written as a text."""


def invalid(field: str, code: ErrorCode = ErrorCode.INVALID_CONFIGURATION) -> DomainError:
    """Return the refusal that names ``field`` (never a value)."""
    return DomainError(code, field)


def text_of(values: Mapping[str, Value], name: str) -> str:
    """Return a text field of ``values``, or the empty text when it is not one."""
    value = values.get(name)
    return value if isinstance(value, str) else ""


def strings_of(values: Mapping[str, Value], name: str) -> list[str]:
    """Return the texts of a list field of ``values`` (empty when it is absent)."""
    value = values.get(name)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def int_of(values: Mapping[str, Value], name: str) -> int | None:
    """Return a whole-number field of ``values``, ``None`` when it is absent, null or no number."""
    value = values.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def flag_of(values: Mapping[str, Value], name: str, default: bool) -> bool:
    """Return a true-or-false field of ``values``, ``default`` when it is absent or no flag."""
    value = values.get(name)
    return value if isinstance(value, bool) else default


def object_of(values: Mapping[str, Value], name: str) -> dict[str, Value]:
    """Return an object field of ``values`` (empty when it is absent or no object)."""
    value = values.get(name)
    return dict(value) if isinstance(value, dict) else {}


def json_list(items: Iterable[str]) -> Json:
    """Return texts as the JSON list a field holds."""
    return list[Json](items)


def json_texts(texts: Mapping[str, str]) -> Json:
    """Return a map of texts as the JSON object a field holds."""
    return dict[str, Json](texts)


def utc_moment(value: Value, field: str) -> str:
    """Read an ISO date and time with an offset and return it as UTC ISO text.

    Raises:
        DomainError: ``invalid_configuration`` naming ``field`` for anything else, a time
            without an offset included.
    """
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        raise invalid(field) from None
    if moment.tzinfo is None:
        raise invalid(field)
    return moment.astimezone(UTC).isoformat()


def text_map(
    value: object,
    field: str,
    *,
    code: ErrorCode = ErrorCode.INVALID_CONFIGURATION,
    field_of: Callable[[str], str] | None = None,
) -> dict[str, str]:
    """Return a JSON object of texts (per locale), or refuse.

    Raises:
        DomainError: ``code`` naming ``field``, or the field ``field_of`` gives for the
            locale whose text is not one.
    """
    if not isinstance(value, Mapping):
        raise invalid(field, code)
    texts: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise invalid(field_of(str(key)) if field_of and isinstance(key, str) else field, code)
        texts[key] = item
    return texts


def int_setting(context: RuleContext, key: str) -> int:
    """Return a whole-number setting as the installation runs it, else the registry default.

    The adapter fills ``context.settings`` with the effective values; a rule never keeps a
    copy of a default, so the registry stays the one source.
    """
    value = context.settings.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(cast("int", BY_KEY[key].default))


def text_setting(context: RuleContext, key: str) -> str:
    """Return a text setting as the installation runs it, else the registry default."""
    value = context.settings.get(key)
    if isinstance(value, str):
        return value
    default = BY_KEY[key].default
    return default if isinstance(default, str) else ""


def effective_idp_key(fields: Mapping[str, Value], slug: str) -> str:
    """Return the value an identity provider knows an object by: its idp key, else its slug."""
    return str(fields.get("idp_key") or slug)


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


def setting_entry_check(lookup: Callable[[str], SettingSpec | None]) -> KindCheck:
    """Return the kind check of a settings map: a key ``lookup`` knows, a value within bounds.

    Args:
        lookup: Gives the registry entry of a key a document may set, else ``None``.
    """

    def check(
        merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
    ) -> Mapping[str, Value]:
        key = text_of(merged, "key")
        spec = lookup(key)
        if spec is None:
            raise DomainError(ErrorCode.SETTING_NOT_EDITABLE, key)
        value = setting_value(spec, merged["value"], production=context.production)
        return {**merged, "value": value}

    return check


def text_override_check(clean: Callable[[str, str], str]) -> KindCheck:
    """Return the kind check of a texts map: a locale of the installation, then ``clean``.

    Args:
        clean: The portal's own cleaning of one text, given its key and its text.
    """

    def check(
        merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
    ) -> Mapping[str, Value]:
        if merged["locale"] not in context.locales:
            raise invalid("locale")
        return {**merged, "text": clean(str(merged["key"]), str(merged["text"]))}

    return check


def term_pair_refusals(
    texts: Iterable[Entity],
    path: Callable[[str, str], str],
    *,
    locales: Collection[str] | None = None,
) -> Iterator[Refusal]:
    """Refuse a locale where only one of the two words of the agent term would be set.

    Args:
        texts: The text entries (key ``(locale, key)``, field ``text``) of one owner.
        path: Gives the path of the entry to blame from a locale and the key refused.
        locales: Only these locales, when given (those a plan touches).
    """
    words: dict[str, dict[str, str | None]] = {}
    for entry in texts:
        locale, key = entry.key
        if key in TERM_KEYS and (locales is None or locale in locales):
            pair = words.setdefault(locale, dict.fromkeys(TERM_KEYS))
            pair[key] = str(entry.fields.get("text"))
    for locale, pair in sorted(words.items()):
        try:
            check_term_pair(pair)
        except DomainError as error:
            yield Refusal(path(locale, str(error.field)), error.code, error.field)


def combination_refusal(
    resolution: Resolution, values: Mapping[str, object], path: str, field: str | None = None
) -> Refusal | None:
    """Return the refusal for settings that break a cross-field rule together (ADR 0042).

    The field is ``field`` or, without one, the name of the rule that is broken. ``None`` when
    the rule is not available (the layer that reads the environment supplies it) or the values
    pass.
    """
    check = resolution.context.combination_problem
    problem = None if check is None else check(values)
    if problem is None:
        return None
    return Refusal(path, ErrorCode.SETTING_COMBINATION, field or problem)


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
