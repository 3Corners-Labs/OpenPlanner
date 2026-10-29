"""The editable text blocks of citizen messages, per group and locale (ADR 0009, ADR 0022).

A message keeps its fixed, accessible layout; admins change only text blocks. A group text
replaces the catalogue text of one block in one locale; removing it brings the default back.
Lookup when a message is composed: group text → organisation override → catalogue.

Placeholders are ``{name}`` from the block's allow-list only; a text may leave some out, but
never use another one. The rule is checked here, so no stored text can ask the composer for a
value it does not have. (``app.i18n.formatting`` parses the same syntax; the domain may not
import it, ADR 0004.)
"""

import re
from dataclasses import dataclass

from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode

MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 2000
MAX_SMS_LENGTH = 320
"""Before the placeholders are filled; leaves room for the link within three SMS segments."""

EMAIL_PLACEHOLDERS = frozenset({"date", "time", "group_name"})
SMS_PLACEHOLDERS = frozenset({"first_name", "date", "time", "group_name", "manage_url"})
CANCELLATION_SMS_PLACEHOLDERS = frozenset({"first_name", "date", "time", "group_name", "reason"})
"""A cancelled booking has nothing left to manage; a planner's reason may be added (ADR 0044)."""

_TOKEN = re.compile(r"\{\{|\}\}|\{([^{}]*)\}?|\}")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_NEWLINE = re.compile(r"[\r\n]")


@dataclass(frozen=True)
class TextBlock:
    """One editable block of a message.

    Attributes:
        key: Catalogue key of the default text, e.g. ``message.reminder.intro``.
        purpose: ``confirmation``, ``change``, ``reminder``, ``cancellation``, ``reason``
            (a planner's reasons, ADR 0044) or ``all``.
        channel: The channel the block is sent on.
        part: ``subject``, ``intro``, ``signoff``, ``body`` (SMS) or ``reason_<code>``.
        placeholders: The ``{name}``s the text may use.
        max_length: Maximum length before the placeholders are filled.
        single_line: Subjects may not contain line breaks (header injection).
    """

    key: str
    purpose: str
    channel: ChannelKind
    part: str
    placeholders: frozenset[str]
    max_length: int
    single_line: bool = False


def _email(purpose: str, part: str, *, subject: bool = False) -> TextBlock:
    """Return an email block whose key follows ``message.<purpose>.<part>``."""
    return TextBlock(
        f"message.{purpose}.{part}",
        purpose,
        ChannelKind.EMAIL,
        part,
        EMAIL_PLACEHOLDERS,
        MAX_SUBJECT_LENGTH if subject else MAX_BODY_LENGTH,
        single_line=subject,
    )


MESSAGE_BLOCKS: tuple[TextBlock, ...] = (
    _email("confirmation", "subject", subject=True),
    _email("confirmation", "intro"),
    _email("change", "subject", subject=True),
    _email("change", "intro"),
    # ADR 0044: a planner's move also goes out as an SMS where the citizen can get one.
    TextBlock(
        "message.change.sms", "change", ChannelKind.SMS, "body", SMS_PLACEHOLDERS, MAX_SMS_LENGTH
    ),
    _email("reminder", "subject", subject=True),
    _email("reminder", "intro"),
    TextBlock(
        "message.reminder.sms",
        "reminder",
        ChannelKind.SMS,
        "body",
        SMS_PLACEHOLDERS,
        MAX_SMS_LENGTH,
    ),
    _email("cancellation", "subject", subject=True),
    _email("cancellation", "intro"),
    TextBlock(
        "message.cancellation.sms",
        "cancellation",
        ChannelKind.SMS,
        "body",
        CANCELLATION_SMS_PLACEHOLDERS,
        MAX_SMS_LENGTH,
    ),
    # ADR 0044: the fixed reasons a planner chooses from; never free text.
    *(
        TextBlock(
            f"message.reason.{code}",
            "reason",
            ChannelKind.EMAIL,
            f"reason_{code}",
            frozenset(),
            MAX_SUBJECT_LENGTH,
            single_line=True,
        )
        for code in ("staffing", "technical", "unforeseen")
    ),
    TextBlock(
        "message.signoff",
        "all",
        ChannelKind.EMAIL,
        "signoff",
        frozenset({"group_name"}),
        MAX_SUBJECT_LENGTH,
        single_line=True,
    ),
)
"""Every editable block, in the order the editor shows them."""

BLOCKS_BY_KEY = {block.key: block for block in MESSAGE_BLOCKS}


def _invalid(key: str) -> DomainError:
    """Return the error for an invalid text of block ``key`` (never with the text)."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, key)


def used_placeholders(text: str) -> frozenset[str] | None:
    """Return the placeholder names in ``text``, or ``None`` if a brace is malformed.

    ``{{`` and ``}}`` are literal braces; anything else between braces must be a name.
    """
    names: set[str] = set()
    for match in _TOKEN.finditer(text):
        piece = match.group(0)
        if piece in ("{{", "}}"):
            continue
        name = match.group(1)
        if name is None or not piece.endswith("}") or not _NAME.fullmatch(name):
            return None
        names.add(name)
    return frozenset(names)


def clean_text(key: str, text: str) -> str:
    """Return the trimmed text for block ``key`` if it may be stored.

    Rules: a known block, not blank, within the block's length, no control characters, no
    line breaks in single-line blocks, and only placeholders from the block's allow-list.

    Raises:
        DomainError: ``invalid_configuration`` with the block key as field.
    """
    block = BLOCKS_BY_KEY.get(key)
    if block is None:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "key")
    cleaned = text.strip().replace("\r\n", "\n")
    names = used_placeholders(cleaned)
    if (
        not cleaned
        or len(cleaned) > block.max_length
        or _CONTROL_CHARS.search(cleaned)
        or (block.single_line and _NEWLINE.search(cleaned))
        or names is None
        or not names <= block.placeholders
    ):
        raise _invalid(key)
    return cleaned


GroupTexts = dict[tuple[str, str], str]
"""A group's own texts by ``(locale, block key)``."""
