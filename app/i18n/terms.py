"""The agent term as placeholders of a text (ADR 0077).

The words for the person who takes the call are catalogue texts of ``term.agent`` and
``term.agents``; an organisation or a group may replace them (``app.domain.agent_term`` holds
the rules). A text uses them as ``{agent}``, ``{agents}``, ``{Agent}`` and ``{Agents}``, the
capitalised forms where the word starts a sentence or a label. The translator fills the four
from the words in effect, so a caller never passes them. ``tests/i18n/test_terms.py`` keeps the
keys equal to those of the domain, which this package may not import (ADR 0004).
"""

from collections.abc import Callable

SINGULAR_KEY = "term.agent"
PLURAL_KEY = "term.agents"


def capitalise(word: str) -> str:
    """Return ``word`` with its first letter in capitals; the rest is left as it is."""
    return word[:1].upper() + word[1:]


class AgentTermPlaceholders:
    """``DerivedPlaceholders`` for the agent term."""

    names = frozenset({"agent", "agents", "Agent", "Agents"})

    def values(self, lookup: Callable[[str], str]) -> dict[str, str]:
        """Return the four placeholders for the words ``lookup`` gives."""
        singular, plural = lookup(SINGULAR_KEY), lookup(PLURAL_KEY)
        return {
            "agent": singular,
            "agents": plural,
            "Agent": capitalise(singular),
            "Agents": capitalise(plural),
        }
