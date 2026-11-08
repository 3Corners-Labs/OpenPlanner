"""The filter of the list pattern (ADR 0047 §4, §8): one rule for every admin list.

A list narrows its rows with a GET search text and, where rows belong to organisations, an
organisation choice. The text is cut to ``MAX_QUERY_LENGTH``, trimmed and matched
case-insensitively as a substring of the row's fields. A list never shows the text again, so a
personal-data value typed into the address bar is not reflected (ADR 0063 §3); it only says
*that* a search is active. The organisation counts only when one of the rows belongs to it; any
other value is dropped and never echoed (``docs/threat-model.md``).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.admin.forms import Option

MAX_QUERY_LENGTH = 100
"""Longest search text a list reads; names are shorter, the rest is cut off (a fixed rule)."""

QUERY_PARAM = "q"
"""The name of the search field in every list's GET form."""

ORGANISATION_PARAM = "org"
"""The name of the organisation choice in every list's GET form."""


@dataclass(frozen=True)
class ListFilter:
    """What narrows a list: whether a search is active, and the organisation chosen.

    Attributes:
        searching: Whether a search text narrows the rows (the text itself is not kept).
        organisation: The chosen organisation's slug, or empty.
        organisations: The organisations of the rows, to choose from.
        max_length: The longest search text, for the field's ``maxlength``.
    """

    searching: bool
    organisation: str
    organisations: list[Option]
    max_length: int = MAX_QUERY_LENGTH

    @property
    def active(self) -> bool:
        """Whether anything narrows the list."""
        return self.searching or bool(self.organisation)


def search_text(raw: str) -> str:
    """Return the search text of ``raw`` as a list reads it: cut off and trimmed."""
    return raw[:MAX_QUERY_LENGTH].strip()


def matches(text: str, *fields: str) -> bool:
    """Whether the search ``text`` is empty or occurs in any of ``fields``, ignoring case."""
    folded = text.casefold()
    return not folded or any(folded in field.casefold() for field in fields)


def narrow[T](
    rows: Sequence[T],
    query: str,
    organisation: str,
    *,
    organisation_of: Callable[[T], tuple[str, str]],
    fields: Callable[[T], tuple[str, ...]],
) -> tuple[list[T], ListFilter]:
    """Return the rows matching ``query`` and ``organisation``, and the checked filter.

    Args:
        rows: Every row the reader may see.
        query: The raw search text from the request.
        organisation: The raw organisation choice from the request.
        organisation_of: A row's organisation as ``(slug, name)``.
        fields: The texts of a row the search looks in.
    """
    present = dict(organisation_of(row) for row in rows)
    chosen = organisation if organisation in present else ""
    text = search_text(query)
    kept = [
        row
        for row in rows
        if (not chosen or organisation_of(row)[0] == chosen) and matches(text, *fields(row))
    ]
    options = [Option(slug, name) for slug, name in present.items()]
    return kept, ListFilter(bool(text), chosen, options)
