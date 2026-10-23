"""Safe placeholder formatting and validation of admin-edited texts.

Messages use ``{name}`` placeholders (lower case, letters, digits, underscore, not starting
with an underscore) and ``{{`` / ``}}`` for literal braces. Unlike ``str.format`` there is no
attribute access, indexing, conversion or format spec, so a text edited in the admin UI
cannot reach object internals (``{x.__class__}`` stays literal text).
"""

import re
from collections.abc import Mapping

MAX_TEXT_LENGTH = 2000
"""Upper bound for a single admin-edited text, in characters."""

_TOKEN = re.compile(r"\{\{|\}\}|\{([a-z][a-z0-9_]*)\}")


class InvalidOverride(ValueError):  # noqa: N818 - reads as a domain term in the admin UI
    """An admin-edited text violates the rules in ``validate_override``."""


def placeholders(text: str) -> frozenset[str]:
    """Return the names of the ``{name}`` placeholders in ``text``."""
    return frozenset(match.group(1) for match in _TOKEN.finditer(text) if match.group(1))


def format_message(text: str, params: Mapping[str, object]) -> str:
    """Substitute ``{name}`` placeholders in ``text`` with ``str(params[name])``.

    The result is plain text; escaping for HTML happens at render time (Jinja2 autoescape).

    Raises:
        KeyError: If a placeholder has no value in ``params`` (a programming error).
    """

    def substitute(match: re.Match[str]) -> str:
        piece = match.group(0)
        if piece == "{{":
            return "{"
        if piece == "}}":
            return "}"
        return str(params[match.group(1)])

    return _TOKEN.sub(substitute, text)


def validate_override(*, source: str, override: str) -> None:
    """Check that ``override`` may replace the shipped text ``source``.

    Rules: not blank, at most ``MAX_TEXT_LENGTH`` characters, and exactly the same
    placeholders as ``source`` (so templates keep receiving the values they need).

    Raises:
        InvalidOverride: With a message naming the violated rule.
    """
    if not override.strip():
        raise InvalidOverride("text is empty")
    if len(override) > MAX_TEXT_LENGTH:
        raise InvalidOverride(f"text is too long (max {MAX_TEXT_LENGTH} characters)")
    expected, actual = placeholders(source), placeholders(override)
    if expected != actual:
        raise InvalidOverride(
            f"placeholders differ: expected {sorted(expected)}, got {sorted(actual)}"
        )
