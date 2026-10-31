"""The editable text blocks of participant messages, per group and locale (ADR 0009, ADR 0022).

A message keeps its fixed, accessible layout; admins change only text blocks. A group text
replaces the catalogue text of one block in one locale; removing it brings the default back.
Lookup when a message is composed: group text → organisation override → catalogue (the
organisation's texts are ``app.domain.text_overrides``).

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

EVERYWHERE_PLACEHOLDERS = frozenset({"agent", "agents", "Agent", "Agents"})
"""Placeholders every text may use besides its own: the agent term, which the translator fills
from the words in effect for the organisation and group (ADR 0077)."""

EMAIL_PLACEHOLDERS = frozenset({"date", "time", "group_name"})
SMS_PLACEHOLDERS = frozenset({"first_name", "date", "time", "group_name", "manage_url"})
CANCELLATION_SMS_PLACEHOLDERS = frozenset({"first_name", "date", "time", "group_name", "reason"})
REBOOK_EMAIL_PLACEHOLDERS = EMAIL_PLACEHOLDERS | {"deadline"}
REBOOK_SMS_PLACEHOLDERS = SMS_PLACEHOLDERS | {"deadline", "reason"}
LAPSED_SMS_PLACEHOLDERS = frozenset({"first_name", "date", "time", "group_name"})
"""ADR 0044: a request to rebook names when it lapses; a lapsed one has no link left."""
"""A cancelled booking has nothing left to manage; a planner's reason may be added (ADR 0044)."""

_TOKEN = re.compile(r"\{\{|\}\}|\{([^{}]*)\}?|\}")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_NEWLINE = re.compile(r"[\r\n]")


@dataclass(frozen=True)
class TextBlock:
    """One editable block of a message.

    Attributes:
        key: Catalogue key of the default text, e.g. ``message.reminder.intro``.
        purpose: ``confirmation``, ``change``, ``reminder``, ``cancellation``, the planner's
            ``rebook_request``, ``rebook_reminder``, ``rebook_lapsed`` and ``reason``
            (ADR 0044), or ``all``.
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
    # ADR 0044: a planner's move also goes out as an SMS where the participant can get one.
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
    # ADR 0044: a planner's request to choose a new time, its one reminder and its lapse.
    *(
        block
        for purpose, email, sms in (
            ("rebook_request", REBOOK_EMAIL_PLACEHOLDERS, REBOOK_SMS_PLACEHOLDERS),
            ("rebook_reminder", REBOOK_EMAIL_PLACEHOLDERS, REBOOK_SMS_PLACEHOLDERS),
            ("rebook_lapsed", EMAIL_PLACEHOLDERS, LAPSED_SMS_PLACEHOLDERS),
        )
        for block in (
            TextBlock(
                f"message.{purpose}.subject",
                purpose,
                ChannelKind.EMAIL,
                "subject",
                email,
                MAX_SUBJECT_LENGTH,
                single_line=True,
            ),
            TextBlock(
                f"message.{purpose}.intro",
                purpose,
                ChannelKind.EMAIL,
                "intro",
                email,
                MAX_BODY_LENGTH,
            ),
            TextBlock(
                f"message.{purpose}.sms", purpose, ChannelKind.SMS, "body", sms, MAX_SMS_LENGTH
            ),
        )
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


def check_text(
    key: str, text: str, placeholders: frozenset[str], max_length: int, *, single_line: bool
) -> str:
    """Return the trimmed ``text`` of block ``key`` if it may be stored.

    Rules: not blank, within ``max_length``, no control characters, no line breaks when
    ``single_line``, and only placeholders from ``placeholders`` or the agent term. One home for
    the rules of group texts and organisation texts (ADR 0004 §8, ADR 0009).

    Raises:
        DomainError: ``invalid_configuration`` with the block key as field.
    """
    cleaned = text.strip().replace("\r\n", "\n")
    names = used_placeholders(cleaned)
    if (
        not cleaned
        or len(cleaned) > max_length
        or _CONTROL_CHARS.search(cleaned)
        or (single_line and _NEWLINE.search(cleaned))
        or names is None
        or not names <= placeholders | EVERYWHERE_PLACEHOLDERS
    ):
        raise _invalid(key)
    return cleaned


def clean_text(key: str, text: str) -> str:
    """Return the trimmed text for message block ``key`` if it may be stored.

    Raises:
        DomainError: ``invalid_configuration`` with the block key as field, or ``key`` for a
            key that is no block.
    """
    block = BLOCKS_BY_KEY.get(key)
    if block is None:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "key")
    return check_text(
        key, text, block.placeholders, block.max_length, single_line=block.single_line
    )


GroupTexts = dict[tuple[str, str], str]
"""A group's own texts by ``(locale, block key)``."""
