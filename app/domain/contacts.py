"""Alternative ways to reach an organisation: the contact block (ADR 0037 §1–2).

A ``ContactBlock`` tells participants how else to reach the organisation when they cannot book: on
the page of an emergency closure (ADR 0037) and on the maintenance page (ADR 0038). It holds
up to ``MAX_OPTIONS`` structured ``ContactOption``s, never free HTML (ADR 0009):

    phone    digits, spaces, ``+ - ( )``        rendered as a ``tel:`` link
    email    a functional address               rendered as a ``mailto:`` link
    website  a plain ``https://`` URL           rendered as a link
    chat     a plain ``https://`` URL           rendered as a link
    address  one to four lines                  rendered as an address block

Every option may have a label and opening hours per locale. Contact options are organisation
data shown publicly, not personal data of participants; the admin page says that numbers and
addresses must be functional ones. Refusals name the offending part (``phone``, ``url``,
``label`` ...), and ``numbered_option`` prefixes the form row (``contact_3_url``).
"""

import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit

from app.domain.errors import DomainError, ErrorCode

CONTACT_LOCALES = ("nl", "en")
"""Locales a label, opening hours or heading may be given in; Dutch is the fallback."""

MAX_OPTIONS = 6
MAX_HEADING_LENGTH = 120
MAX_LABEL_LENGTH = 80
MAX_HOURS_LENGTH = 200
MAX_ADDRESS_LINES = 4
MAX_ADDRESS_LINE_LENGTH = 80
MAX_URL_LENGTH = 500
MAX_EMAIL_LENGTH = 254
MAX_PHONE_LENGTH = 30
MIN_PHONE_DIGITS = 3
"""Short numbers such as 112 and 14 020 are valid; a number has at most 15 digits (E.164)."""
MAX_PHONE_DIGITS = 15

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_PHONE = re.compile(r"\+?[0-9 ()\-]+")
_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)
_URL_FORBIDDEN = re.compile(r"[\s\"'<>`\\\x00-\x1f\x7f]")


class ContactKind(StrEnum):
    """The kinds of contact option; each has its own validation and rendering."""

    PHONE = "phone"
    EMAIL = "email"
    WEBSITE = "website"
    CHAT = "chat"
    ADDRESS = "address"


def _invalid(reason: str) -> DomainError:
    """Return the refusal for the part ``reason`` of a contact option or block."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, reason)


def _localized(texts: Mapping[str, str], limit: int, reason: str) -> dict[str, str]:
    """Return the non-empty single-line texts per known locale, or refuse ``reason``."""
    kept: dict[str, str] = {}
    for locale, raw in texts.items():
        text = raw.strip()
        if not text:
            continue
        if locale not in CONTACT_LOCALES or len(text) > limit or _CONTROL_CHARS.search(text):
            raise _invalid(reason)
        kept[locale] = text
    return kept


def _in_locale(texts: Mapping[str, str], locale: str) -> str | None:
    """Return the text in ``locale``, falling back to Dutch, or ``None``."""
    return texts.get(locale) or texts.get(CONTACT_LOCALES[0])


def _normalise(kind: ContactKind, raw: str) -> str:
    """Return ``raw`` the way it is stored: trimmed, phone spaces and address lines tidied."""
    if kind is ContactKind.PHONE:
        return " ".join(raw.split())
    if kind is ContactKind.ADDRESS:
        lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        return "\n".join(line.strip() for line in lines if line.strip())
    return raw.strip()


def _check_phone(value: str) -> None:
    """Refuse ``phone`` unless the number has only number characters and 3 to 15 digits."""
    digits = sum(c.isdigit() for c in value)
    if (
        len(value) > MAX_PHONE_LENGTH
        or not _PHONE.fullmatch(value)
        or not MIN_PHONE_DIGITS <= digits <= MAX_PHONE_DIGITS
    ):
        raise _invalid("phone")


def _check_email(value: str) -> None:
    """Refuse ``email`` unless it is a plain address with a dotted domain."""
    if len(value) > MAX_EMAIL_LENGTH or not _EMAIL.fullmatch(value):
        raise _invalid("email")


def _check_url(value: str) -> None:
    """Refuse ``url`` unless it is a plain ``https`` link with a host and no credentials."""
    if len(value) > MAX_URL_LENGTH or _URL_FORBIDDEN.search(value):
        raise _invalid("url")
    parts = urlsplit(value)
    if parts.scheme.lower() != "https" or not parts.hostname or parts.username is not None:
        raise _invalid("url")


def _check_address(value: str) -> None:
    """Refuse ``address`` unless it has one to four short single lines."""
    lines = value.split("\n") if value else []
    if not 1 <= len(lines) <= MAX_ADDRESS_LINES or any(
        len(line) > MAX_ADDRESS_LINE_LENGTH or _CONTROL_CHARS.search(line) for line in lines
    ):
        raise _invalid("address")


_CHECKS: dict[ContactKind, Callable[[str], None]] = {
    ContactKind.PHONE: _check_phone,
    ContactKind.EMAIL: _check_email,
    ContactKind.WEBSITE: _check_url,
    ContactKind.CHAT: _check_url,
    ContactKind.ADDRESS: _check_address,
}


@dataclass(frozen=True)
class ContactOption:
    """One way to reach the organisation. Build from form input with ``create``.

    Attributes:
        kind: What kind of contact it is.
        value: The number, address or URL, as stored (``create`` tidies it); address lines
            are separated by line breaks.
        labels: Optional label per locale (``Klantcontact``), single line.
        hours: Optional opening hours per locale (``ma-vr 9:00-17:00``), single line.

    Raises:
        DomainError: ``invalid_configuration`` naming ``phone``, ``email``, ``url``,
            ``address``, ``label`` or ``hours``.
    """

    kind: ContactKind
    value: str
    labels: Mapping[str, str] = field(default_factory=dict)
    hours: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the value for its kind and the texts."""
        _CHECKS[self.kind](self.value)
        _localized(self.labels, MAX_LABEL_LENGTH, "label")
        _localized(self.hours, MAX_HOURS_LENGTH, "hours")

    @classmethod
    def create(
        cls,
        kind: ContactKind,
        value: str,
        *,
        labels: Mapping[str, str] | None = None,
        hours: Mapping[str, str] | None = None,
    ) -> "ContactOption":
        """Return a validated option from what an admin typed; empty texts are dropped.

        Raises:
            DomainError: As the class.
        """
        return cls(
            kind,
            _normalise(kind, value),
            _localized(labels or {}, MAX_LABEL_LENGTH, "label"),
            _localized(hours or {}, MAX_HOURS_LENGTH, "hours"),
        )

    @property
    def href(self) -> str | None:
        """Return the link target (``tel:``, ``mailto:`` or the URL); ``None`` for addresses."""
        if self.kind is ContactKind.PHONE:
            number = self.value.replace("(0)", "") if self.value.startswith("+") else self.value
            digits = "".join(c for c in number if c.isdigit())
            return f"tel:{'+' if number.startswith('+') else ''}{digits}"
        if self.kind is ContactKind.EMAIL:
            return f"mailto:{self.value}"
        if self.kind is ContactKind.ADDRESS:
            return None
        return self.value

    @property
    def lines(self) -> tuple[str, ...]:
        """Return the value split into lines (one line except for addresses)."""
        return tuple(self.value.split("\n"))

    def label(self, locale: str) -> str | None:
        """Return the label in ``locale``, falling back to Dutch, or ``None``."""
        return _in_locale(self.labels, locale)

    def hours_text(self, locale: str) -> str | None:
        """Return the opening hours in ``locale``, falling back to Dutch, or ``None``."""
        return _in_locale(self.hours, locale)


def numbered_option(
    index: int, kind: str, value: str, labels: Mapping[str, str], hours: Mapping[str, str]
) -> ContactOption:
    """Return the option of form row ``index``; refusals name the row (``contact_2_url``).

    Raises:
        DomainError: ``invalid_configuration`` with ``contact_{index}_{part}``, where part is
            ``kind`` for an unknown kind or the part named by ``ContactOption``.
    """
    try:
        return ContactOption.create(ContactKind(kind), value, labels=labels, hours=hours)
    except ValueError:
        raise _invalid(f"contact_{index}_kind") from None
    except DomainError as error:
        raise _invalid(f"contact_{index}_{error.field}") from None


@dataclass(frozen=True)
class ContactBlock:
    """The alternative contact options of an organisation, in the admin's order.

    Attributes:
        options: At most ``MAX_OPTIONS`` options; none means "no block".
        headings: Optional heading per locale; pages fall back to a catalogue text.

    Raises:
        DomainError: ``invalid_configuration`` with ``contacts`` (too many options) or
            ``contacts_heading``.
    """

    options: tuple[ContactOption, ...]
    headings: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the number of options; keep only non-empty headings."""
        if len(self.options) > MAX_OPTIONS:
            raise _invalid("contacts")
        headings = _localized(self.headings, MAX_HEADING_LENGTH, "contacts_heading")
        object.__setattr__(self, "headings", headings)

    @property
    def is_empty(self) -> bool:
        """Return whether the block has no options (and is not shown)."""
        return not self.options

    @property
    def counts(self) -> str:
        """Return the number of options per kind for audit events: ``chat:1,phone:2``."""
        counted = Counter(option.kind.value for option in self.options)
        return ",".join(f"{kind}:{count}" for kind, count in sorted(counted.items()))

    def heading(self, locale: str) -> str | None:
        """Return the heading in ``locale``, falling back to Dutch, or ``None``."""
        return _in_locale(self.headings, locale)
