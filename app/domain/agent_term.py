"""The word for the person who takes the video call, and the rules for changing it (ADR 0077).

In code, the docs and the matrix this person is the *agent*. What a participant or a member of
staff reads is the agent *term*: a singular and a plural per locale that an organisation may
set for all its groups, and a group may set for itself ("behandelaar", "medewerker",
"agent"). The shipped words are the catalogue texts of ``term.agent`` and ``term.agents``; the
texts and the mails reach them through the placeholders ``{agent}``, ``{agents}``, ``{Agent}``
and ``{Agents}`` (the capitalised forms where the word starts a sentence or a label), which
``app.i18n.terms`` fills.

The term is a display word only. It names no right: ``app.domain.authorization`` never reads
it, and the audit log and the exports keep the role code.
"""

from collections.abc import Mapping

from app.domain.errors import DomainError, ErrorCode
from app.domain.message_texts import EVERYWHERE_PLACEHOLDERS, check_text

TERM_SINGULAR = "term.agent"
TERM_PLURAL = "term.agents"
TERM_KEYS = (TERM_SINGULAR, TERM_PLURAL)
"""The catalogue keys of the term; the same keys hold an organisation's or a group's own words."""

TERM_PLACEHOLDERS = EVERYWHERE_PLACEHOLDERS
"""The placeholders a text may use for the term; the translator fills them."""

MIN_TERM_LENGTH = 2
MAX_TERM_LENGTH = 40
"""Bounds of one word. Fixed: they follow from the layout of the matrix column and the top bar
(ADR 0077 *Settings*), like the length limits of the other organisation texts (ADR 0074)."""


def clean_term(key: str, text: str) -> str:
    """Return the trimmed word ``text`` for ``key`` if it may be stored.

    Rules: one of the two term keys, at least ``MIN_TERM_LENGTH`` and at most
    ``MAX_TERM_LENGTH`` characters, one line, no control characters and no braces (a word never
    holds a placeholder).

    Raises:
        DomainError: ``invalid_configuration`` with field ``key`` for another key, or with the
            key itself for a word that breaks its rules.
    """
    if key not in TERM_KEYS:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "key")
    cleaned = check_text(key, text, frozenset(), MAX_TERM_LENGTH, single_line=True)
    if len(cleaned) < MIN_TERM_LENGTH or "{" in cleaned or "}" in cleaned:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, key)
    return cleaned


def check_term_pair(words: Mapping[str, str | None]) -> None:
    """Refuse a form that sets only one of the two words of the term.

    ``None`` is a word brought back to the shipped one. The singular and the plural change
    together, or both stay as shipped, so a text never mixes "behandelaar" and "medewerkers".

    Raises:
        DomainError: ``invalid_configuration`` with the key of the word that is missing.
    """
    singular, plural = words.get(TERM_SINGULAR), words.get(TERM_PLURAL)
    if TERM_SINGULAR in words and TERM_PLURAL in words and (singular is None) != (plural is None):
        raise DomainError(
            ErrorCode.INVALID_CONFIGURATION, TERM_PLURAL if plural is None else TERM_SINGULAR
        )
