"""Booking form fields an administrator adds per group, and the citizen's answers (ADR 0055).

A field has one type from a closed catalogue; each type has its own validator, so no
administrator writes a regular expression. Every field carries a classification that defaults
to ``pii``. Consumers never read that tag themselves: they ask ``Answers.visible(purpose)``,
the one place that decides which answers a screen, log, export or message may see.

Values are personal data while the booking lives. They are excluded from ``repr`` and errors
name the field key only, never the value.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

import phonenumbers

from app.domain.errors import DomainError, ErrorCode

SHORT_TEXT_MAX_LENGTH = 100
LONG_TEXT_MAX_LENGTH = 1000
MAX_EMAIL_LENGTH = 254
MAX_CHOICE_OPTIONS = 30
MAX_NUMBER_DIGITS = 12
MAX_LABEL_LENGTH = 200
MAX_HINT_LENGTH = 300
REQUIRED_LOCALES = ("nl", "en")
"""Every label needs these (CLAUDE.md: nl default, en required)."""
FALLBACK_LOCALE = "nl"
SYSTEM_KEYS = frozenset({"first_name", "last_name", "email", "phone"})
UNKNOWN_FIELD = "unknown"
"""The field name in errors about keys the form does not have; the key itself is never echoed."""

_KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_CONTROL_CHARS_EXCEPT_NEWLINE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_INTEGER = re.compile(rf"^-?[0-9]{{1,{MAX_NUMBER_DIGITS}}}$")
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# Dutch postcode: no leading zero; SA, SD and SS are never issued.
_POSTCODE_NL = re.compile(r"^([1-9][0-9]{3}) ?(?!SA|SD|SS)([A-Z]{2})$")
_CHECKED = frozenset({"on", "true", "1", "yes"})

# Terms that point at a national identifier or a special category of personal data (GDPR
# art. 9, UAVG art. 46). Matched against keys and labels with spaces, dashes and underscores
# removed. A guard rail, not a guarantee (ADR 0055 point 5).
FORBIDDEN_TERMS = (
    "bsn",
    "burgerservice",
    "sofinummer",
    "sofinr",
    "socialsecurity",
    "paspoort",
    "passport",
    "identiteitsbewijs",
    "identiteitskaart",
    "rijbewijsnummer",
    "medisch",
    "medical",
    "diagnose",
    "diagnosis",
    "gezondheid",
    "health",
    "ziekte",
    "religie",
    "religion",
    "geloof",
    "etnisch",
    "ethnic",
    "afkomst",
    "seksueleaard",
    "sexualorientation",
    "politiekevoorkeur",
    "politicalopinion",
    "vakbond",
    "tradeunion",
    "strafblad",
    "criminalrecord",
    "biometrisch",
    "biometric",
)
_TERM_SEPARATORS = re.compile(r"[\s_\-.]+")


class FieldType(StrEnum):
    """The closed catalogue of field types; a new type is a code change, never a setting."""

    SHORT_TEXT = "short_text"
    LONG_TEXT = "long_text"
    EMAIL = "email"
    PHONE = "phone"
    NUMBER = "number"
    DATE = "date"
    POSTCODE_NL = "postcode_nl"
    CHOICE = "choice"
    CHECKBOX = "checkbox"


ALWAYS_PII = frozenset({FieldType.EMAIL, FieldType.PHONE, FieldType.LONG_TEXT})
"""Types that can never be tagged ``not_pii``: contact details and free text."""
_BOUNDED = frozenset({FieldType.NUMBER, FieldType.DATE})


class Classification(StrEnum):
    """Whether a field holds personal data; ``pii`` is the default and the safe side."""

    PII = "pii"
    NOT_PII = "not_pii"


class Purpose(StrEnum):
    """What a consumer wants the answers for; ``Answers.visible`` has one rule per purpose."""

    AGENT_VIEW = "agent_view"
    PLANNER_VIEW = "planner_view"
    EXPORT = "export"
    MESSAGE_PLACEHOLDER = "message_placeholder"
    AUDIT = "audit"
    LOG = "log"
    DISPATCH = "dispatch"


class _Visibility(StrEnum):
    ALL = "all"
    NOT_PII_ONLY = "not_pii_only"
    NOTHING = "nothing"


# ADR 0055 point 7. Total over ``Purpose``: a test fails if a purpose has no rule.
_VISIBILITY: Mapping[Purpose, _Visibility] = MappingProxyType(
    {
        # ADR 0043: an agent in the own queue sees the first name only, so no PII answers.
        Purpose.AGENT_VIEW: _Visibility.NOT_PII_ONLY,
        # The booking page of the agenda, behind ``bookings.view`` (a PII permission).
        Purpose.PLANNER_VIEW: _Visibility.ALL,
        Purpose.EXPORT: _Visibility.NOT_PII_ONLY,
        Purpose.MESSAGE_PLACEHOLDER: _Visibility.NOT_PII_ONLY,
        Purpose.AUDIT: _Visibility.NOT_PII_ONLY,
        Purpose.LOG: _Visibility.NOT_PII_ONLY,
        Purpose.DISPATCH: _Visibility.NOTHING,
    }
)

AnswerValue = str | int | bool


def _definition_error(field_name: str) -> DomainError:
    """Return the error for an invalid field definition attribute."""
    return DomainError(ErrorCode.INVALID_FORM_FIELD, field_name)


def check_label(text: str) -> None:
    """Refuse a key or label that asks for an identifier or a special category of data.

    Raises:
        DomainError: ``form_field_forbidden`` with field ``label``.
    """
    squashed = _TERM_SEPARATORS.sub("", text.casefold())
    if any(term in squashed for term in FORBIDDEN_TERMS):
        raise DomainError(ErrorCode.FORM_FIELD_FORBIDDEN, "label")


def normalise_email(raw: str, field_name: str, code: ErrorCode = ErrorCode.INVALID_CONTACT) -> str:
    """Trim and structurally check an email address; shared by ``Contact`` and answers.

    Full deliverability (MX, IDNA) is not checked here; the confirmation mail proves it.

    Raises:
        DomainError: ``code`` naming ``field_name``.
    """
    email = raw.strip()
    local, at, domain = email.partition("@")
    if (
        not at
        or not local
        or "@" in domain
        or "." not in domain.strip(".")
        or len(email) > MAX_EMAIL_LENGTH
        or any(ch.isspace() for ch in email)
        or _CONTROL_CHARS.search(email)
    ):
        raise DomainError(code, field_name)
    return email


def normalise_phone(
    raw: str, region: str, field_name: str, code: ErrorCode = ErrorCode.INVALID_CONTACT
) -> str:
    """Parse a phone number against ``region`` and return it in E.164 (``+31612345678``).

    Raises:
        DomainError: ``code`` naming ``field_name`` if the number is not a valid number.
    """
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        raise DomainError(code, field_name) from None
    if not phonenumbers.is_valid_number(parsed):
        raise DomainError(code, field_name)
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


@dataclass(frozen=True)
class FieldDefinition:
    """One custom field of a group's booking form. Build it with ``FieldDefinition.create``.

    ``minimum``/``maximum`` bound a ``number`` (the value) or a ``date`` (days relative to the
    day of booking, negative is in the past). ``options`` are the keys of a ``choice``.
    ``labels``, ``hints`` and ``option_labels`` hold the administrator's text per locale; the
    citizen sees the request locale, else Dutch. A retired field takes no new answers but keeps
    its labels for the answers already given.
    """

    key: str
    type: FieldType
    required: bool
    classification: Classification
    options: tuple[str, ...]
    minimum: int | None
    maximum: int | None
    labels: Mapping[str, str] = field(default_factory=dict)
    hints: Mapping[str, str] = field(default_factory=dict)
    option_labels: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    retired: bool = False

    @classmethod
    def create(
        cls,
        *,
        key: str,
        type: FieldType,
        labels: Mapping[str, str],
        required: bool = False,
        classification: Classification = Classification.PII,
        options: Iterable[str] = (),
        minimum: int | None = None,
        maximum: int | None = None,
        hints: Mapping[str, str] | None = None,
        option_labels: Mapping[str, Mapping[str, str]] | None = None,
    ) -> "FieldDefinition":
        """Validate a field definition.

        Raises:
            DomainError: ``invalid_form_field`` naming the attribute, or
                ``form_field_forbidden`` for a key or label that asks for forbidden data.
        """
        if not _KEY.fullmatch(key) or key in SYSTEM_KEYS:
            raise _definition_error("key")
        check_label(key)
        if type in ALWAYS_PII and classification is not Classification.PII:
            raise _definition_error("classification")
        option_keys = tuple(options)
        if type is FieldType.CHOICE:
            if (
                not option_keys
                or len(option_keys) > MAX_CHOICE_OPTIONS
                or len(set(option_keys)) != len(option_keys)
                or not all(_KEY.fullmatch(option) for option in option_keys)
            ):
                raise _definition_error("options")
        elif option_keys:
            raise _definition_error("options")
        if type not in _BOUNDED and (minimum is not None or maximum is not None):
            raise _definition_error("minimum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise _definition_error("maximum")
        texts = option_labels or {}
        if set(texts) - set(option_keys):
            raise _definition_error("options")
        return cls(
            key,
            type,
            required,
            classification,
            option_keys,
            minimum,
            maximum,
            _texts(labels, "label", MAX_LABEL_LENGTH, required_locales=REQUIRED_LOCALES),
            _texts(hints or {}, "hint", MAX_HINT_LENGTH),
            {
                option: _texts(
                    texts.get(option, {}),
                    "options",
                    MAX_LABEL_LENGTH,
                    required_locales=REQUIRED_LOCALES,
                )
                for option in option_keys
            },
        )

    def label(self, locale: str) -> str:
        """Return the label in ``locale``, else Dutch, else the key."""
        return _in_locale(self.labels, locale) or self.key

    def hint(self, locale: str) -> str:
        """Return the help text in ``locale``, else Dutch, else empty."""
        return _in_locale(self.hints, locale)

    def option_label(self, option: str, locale: str) -> str:
        """Return the label of a ``choice`` option in ``locale``, else Dutch, else its key."""
        return _in_locale(self.option_labels.get(option, {}), locale) or option


def _texts(
    texts: Mapping[str, str],
    attribute: str,
    max_length: int,
    *,
    required_locales: Iterable[str] = (),
) -> dict[str, str]:
    """Clean the per-locale texts of a field; blank ones are dropped.

    Raises:
        DomainError: ``invalid_form_field`` naming ``attribute`` for a missing locale, a too
            long text or control characters; ``form_field_forbidden`` for a forbidden term.
    """
    cleaned: dict[str, str] = {}
    for locale, raw in texts.items():
        text = " ".join(raw.split())
        if not text:
            continue
        if len(text) > max_length or _CONTROL_CHARS.search(raw):
            raise _definition_error(attribute)
        check_label(text)
        cleaned[locale] = text
    if any(locale not in cleaned for locale in required_locales):
        raise _definition_error(attribute)
    return cleaned


def _in_locale(texts: Mapping[str, str], locale: str) -> str:
    """Return the text in ``locale``, else the Dutch one, else empty."""
    return texts.get(locale) or texts.get(FALLBACK_LOCALE) or ""


@dataclass(frozen=True)
class FormDefinition:
    """The custom fields of one group's form at one ``version`` (bumped on every change).

    ``fields`` are the active fields in form order; ``retired`` are earlier fields kept for
    their labels and to keep their keys from being used again.
    """

    version: int
    fields: tuple[FieldDefinition, ...] = ()
    retired: tuple[FieldDefinition, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        version: int,
        fields: Iterable[FieldDefinition],
        max_custom_fields: int,
        retired: Iterable[FieldDefinition] = (),
    ) -> "FormDefinition":
        """Check the form as a whole: the number of active fields and unique keys.

        Raises:
            DomainError: ``invalid_form_field`` naming ``fields`` or ``key``.
        """
        items = tuple(fields)
        old = tuple(replace(f, retired=True) for f in retired)
        if version < 1 or len(items) > max_custom_fields:
            raise _definition_error("fields")
        keys = [f.key for f in items + old]
        if len(set(keys)) != len(keys):
            raise _definition_error("key")
        return cls(version, items, old)

    def get(self, key: str) -> FieldDefinition | None:
        """Return the active field with ``key``, or ``None``."""
        return next((f for f in self.fields if f.key == key), None)

    def find(self, key: str) -> FieldDefinition | None:
        """Return the active or retired field with ``key``, or ``None``."""
        return self.get(key) or next((f for f in self.retired if f.key == key), None)


@dataclass(frozen=True)
class Answers:
    """The validated answers of one booking, with the form version they were given under.

    Read them only through ``visible``; ``to_storage`` is for the database adapter.
    """

    version: int
    _values: Mapping[str, AnswerValue] = field(repr=False)
    _not_pii: frozenset[str] = field(repr=False)

    def visible(self, purpose: Purpose) -> dict[str, AnswerValue]:
        """Return the answers ``purpose`` may see (ADR 0055 point 7)."""
        rule = _VISIBILITY[purpose]
        if rule is _Visibility.ALL:
            return dict(self._values)
        if rule is _Visibility.NOT_PII_ONLY:
            return {k: v for k, v in self._values.items() if k in self._not_pii}
        return {}

    def to_storage(self) -> dict[str, Any]:
        """Return the JSON document stored in ``Booking.form_answers`` (purged as a whole)."""
        return {
            "version": self.version,
            "answers": dict(self._values),
            "not_pii": sorted(self._not_pii),
        }

    @classmethod
    def from_storage(cls, stored: Mapping[str, Any]) -> "Answers":
        """Rebuild the answers stored by ``to_storage``.

        Only keys listed as ``not_pii`` count as such; anything else, including a key another
        version wrote, counts as PII.
        """
        values: dict[str, AnswerValue] = {
            str(k): v
            for k, v in dict(stored.get("answers", {})).items()
            if isinstance(v, str | int | bool)
        }
        not_pii = frozenset(str(k) for k in stored.get("not_pii", ())) & values.keys()
        return cls(int(stored.get("version", 0)), MappingProxyType(values), not_pii)


def raise_to_pii(stored: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return stored answers in which ``key`` counts as PII again (tag raised or field retired).

    The adapter applies this to every stored answer of the group, so a tag raised later also
    covers the answers given before (ADR 0055 point 4).
    """
    updated = dict(stored)
    updated["not_pii"] = [k for k in stored.get("not_pii", ()) if k != key]
    return updated


def validate_answers(
    form: FormDefinition, raw: Mapping[str, str | None], *, today: date, region: str
) -> Answers:
    """Validate the raw answers to ``form``; the one validator for every entry point.

    Args:
        form: The group's current form definition.
        raw: Field key to the submitted string (``None`` or blank when not answered).
        today: The booking day in the group's time zone, for relative date bounds.
        region: Default region for phone numbers typed without a country code.

    Raises:
        DomainError: ``invalid_answer`` naming the field key, or ``unknown`` for a key the form
            does not have.
    """
    if any(form.get(key) is None for key in raw):
        raise DomainError(ErrorCode.INVALID_ANSWER, UNKNOWN_FIELD)
    values: dict[str, AnswerValue] = {}
    for definition in form.fields:
        value = _validate_one(definition, raw.get(definition.key), today, region)
        if value is not None:
            values[definition.key] = value
    not_pii = frozenset(f.key for f in form.fields if f.classification is Classification.NOT_PII)
    return Answers(form.version, MappingProxyType(values), not_pii & values.keys())


def _validate_one(
    definition: FieldDefinition, raw: str | None, today: date, region: str
) -> AnswerValue | None:
    """Validate one answer; ``None`` means "not answered" and is allowed only if optional."""
    error = DomainError(ErrorCode.INVALID_ANSWER, definition.key)
    if definition.type is FieldType.CHECKBOX:
        return _validate_checkbox(definition, raw, error)
    text = (raw or "").strip()
    if not text:
        if definition.required:
            raise error
        return None
    control = (
        _CONTROL_CHARS_EXCEPT_NEWLINE if definition.type is FieldType.LONG_TEXT else _CONTROL_CHARS
    )
    if control.search(text.replace("\r\n", "\n")):
        raise error
    match definition.type:
        case FieldType.SHORT_TEXT:
            return _bounded_text(" ".join(text.split()), SHORT_TEXT_MAX_LENGTH, error)
        case FieldType.LONG_TEXT:
            return _bounded_text(text.replace("\r\n", "\n"), LONG_TEXT_MAX_LENGTH, error)
        case FieldType.EMAIL:
            return normalise_email(text, definition.key, ErrorCode.INVALID_ANSWER)
        case FieldType.PHONE:
            return normalise_phone(text, region, definition.key, ErrorCode.INVALID_ANSWER)
        case FieldType.NUMBER:
            return _validate_number(definition, text, error)
        case FieldType.DATE:
            return _validate_date(definition, text, today, error)
        case FieldType.POSTCODE_NL:
            match = _POSTCODE_NL.fullmatch(text.upper())
            if match is None:
                raise error
            return f"{match.group(1)} {match.group(2)}"
        case FieldType.CHOICE:
            if text not in definition.options:
                raise error
            return text
    raise error  # pragma: no cover - every FieldType is matched above


def _validate_checkbox(definition: FieldDefinition, raw: str | None, error: DomainError) -> bool:
    """A checkbox is ticked or not; a required one must be ticked (e.g. consent)."""
    text = (raw or "").strip().casefold()
    if text and text not in _CHECKED:
        raise error
    if definition.required and not text:
        raise error
    return bool(text)


def _bounded_text(text: str, max_length: int, error: DomainError) -> str:
    """Return ``text`` if it fits ``max_length``."""
    if len(text) > max_length:
        raise error
    return text


def _validate_number(definition: FieldDefinition, text: str, error: DomainError) -> int:
    """A whole number within the field's bounds."""
    if not _INTEGER.fullmatch(text):
        raise error
    number = int(text)
    if (definition.minimum is not None and number < definition.minimum) or (
        definition.maximum is not None and number > definition.maximum
    ):
        raise error
    return number


def _validate_date(definition: FieldDefinition, text: str, today: date, error: DomainError) -> str:
    """An ISO date (what ``<input type=date>`` sends) within days relative to ``today``."""
    if not _ISO_DATE.fullmatch(text):
        raise error
    try:
        value = date.fromisoformat(text)
    except ValueError:
        raise error from None
    if (definition.minimum is not None and value < today + timedelta(days=definition.minimum)) or (
        definition.maximum is not None and value > today + timedelta(days=definition.maximum)
    ):
        raise error
    return value.isoformat()
