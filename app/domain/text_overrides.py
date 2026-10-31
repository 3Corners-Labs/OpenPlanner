"""The texts an organisation may change for its participants, and the rules for them (ADR 0004).

An organisation override replaces the shipped catalogue text of one key in one locale, for every
group of the organisation; a group's own text (``app.domain.message_texts``) still wins over it.
Only keys on the allow-list below are editable: the message blocks of ADR 0009 and a few
participant-page texts. Each key states the ``{placeholders}`` its text may use and a length,
checked here so no stored text can ask a page or message for a value it does not have, or carry
markup or Jinja: texts are plain text and escaped on output (ADR 0004 §8, ADR 0009). The domain
may not import ``app.i18n``; ``tests/i18n`` checks that each page text's allow-list matches the
placeholders of the shipped nl and en texts.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.agent_term import MAX_TERM_LENGTH, TERM_KEYS, clean_term
from app.domain.errors import DomainError, ErrorCode
from app.domain.message_texts import (
    MAX_BODY_LENGTH,
    MAX_SUBJECT_LENGTH,
    MESSAGE_BLOCKS,
    check_text,
)


class Area(StrEnum):
    """Where an editable text is shown."""

    MESSAGES = "messages"
    PAGES = "pages"
    TERMS = "terms"


@dataclass(frozen=True)
class EditableText:
    """One text an organisation may override.

    Attributes:
        key: Catalogue key of the shipped text.
        area: ``messages`` (email and SMS blocks) or ``pages`` (participant pages).
        placeholders: The ``{name}``s the text may use.
        max_length: Maximum length before the placeholders are filled.
        single_line: Whether line breaks are refused (subjects, headings).
    """

    key: str
    area: Area
    placeholders: frozenset[str]
    max_length: int
    single_line: bool = False


def _page(
    key: str, *placeholders: str, single_line: bool = False, max_length: int = MAX_BODY_LENGTH
) -> EditableText:
    """Return a participant-page text whose placeholders are those of its shipped text."""
    return EditableText(key, Area.PAGES, frozenset(placeholders), max_length, single_line)


PAGE_TEXTS: tuple[EditableText, ...] = (
    _page("group.intro", "minutes"),
    _page("direct.card.title", single_line=True, max_length=MAX_SUBJECT_LENGTH),
    _page("direct.card.text"),
    _page("direct.intro", "group_name"),
    _page("direct.unavailable"),
    _page("booked.title", single_line=True, max_length=MAX_SUBJECT_LENGTH),
    _page("booked.text"),
    _page("booked.lapse"),
    _page("booked.no_mail"),
    _page("confirm.text", "deadline"),
    _page("confirm.not_me"),
    _page("manage.pending.text", "deadline"),
)
"""Participant-page texts, in the order the editor shows them. Each is rendered by a template of a
group page, so it reaches the visitor with the group's organisation (``api.participant``)."""

TERM_TEXTS: tuple[EditableText, ...] = tuple(
    EditableText(key, Area.TERMS, frozenset(), MAX_TERM_LENGTH, single_line=True)
    for key in TERM_KEYS
)
"""The words of the agent term (ADR 0077): singular first, then plural. The same keys hold a
group's own words in ``group_texts``."""

EDITABLE_TEXTS: tuple[EditableText, ...] = (
    *(
        EditableText(b.key, Area.MESSAGES, b.placeholders, b.max_length, b.single_line)
        for b in MESSAGE_BLOCKS
    ),
    *PAGE_TEXTS,
    *TERM_TEXTS,
)
"""Every editable text; the message blocks come first, as in the group editor, and the words
of the agent term last."""

EDITABLE_BY_KEY = {text.key: text for text in EDITABLE_TEXTS}

OrganisationTexts = dict[tuple[str, str], str]
"""An organisation's own texts by ``(locale, key)``."""


def clean_override(key: str, text: str) -> str:
    """Return the trimmed ``text`` for ``key`` if an organisation may store it.

    Raises:
        DomainError: ``invalid_configuration`` with field ``key`` for a key that is not on the
            allow-list, or with the key itself for a text that breaks its rules.
    """
    spec = EDITABLE_BY_KEY.get(key)
    if spec is None:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "key")
    if spec.area is Area.TERMS:
        return clean_term(key, text)
    return check_text(key, text, spec.placeholders, spec.max_length, single_line=spec.single_line)
