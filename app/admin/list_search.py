"""The search field of the list pattern (ADR 0047 §4, §8): one rule for every admin list.

A list narrows its rows with a GET search text. The text is cut to ``MAX_QUERY_LENGTH``,
trimmed and matched case-insensitively as a substring of the row's fields. A list never shows
the text again, so a personal-data value typed into the address bar is not reflected
(ADR 0063 §3); it only says *that* a search is active.
"""

MAX_QUERY_LENGTH = 100
"""Longest search text a list reads; names are shorter, the rest is cut off (a fixed rule)."""

QUERY_PARAM = "q"
"""The name of the search field in every list's GET form."""


def search_text(raw: str) -> str:
    """Return the search text of ``raw`` as a list reads it: cut off and trimmed."""
    return raw[:MAX_QUERY_LENGTH].strip()


def matches(text: str, *fields: str) -> bool:
    """Whether the search ``text`` is empty or occurs in any of ``fields``, ignoring case."""
    folded = text.casefold()
    return not folded or any(folded in field.casefold() for field in fields)
