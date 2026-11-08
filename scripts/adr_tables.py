"""The one reader of ADR *Build status* tables and of ``docs/adr-status.md`` (ADR 0059, 0119).

The decision gate, the issue sync and drift report, the release PR and the status test all
read the same two tables. They read them through this module, so the words a row may use and
the rule that the overview agrees with the tables exist once. Changing a word here changes
what the decision gate accepts, so this file is a gate file itself
(``scripts/decision_gate.py``, ``DECISION_FILES``).

Pure functions on text; no git, no network.
"""

import re
from dataclasses import dataclass

STATES = ("planned", "in PR", "merged", "built", "live", "follow-up", "superseded")
"""What a *Build status* row may start its state with (ADR 0059 point 7, ADR 0119)."""

OPEN_STATES = frozenset({"planned", "follow-up"})
"""Work still to do: each such row has an open issue."""

CODE_STATES = frozenset({"merged", "built", "live"})
"""The step's code is in."""

CLOSED_STATES = CODE_STATES | {"superseded"}
"""Rows whose issue is closed: built, or taken over by another ADR (``superseded``)."""

IN_PR = "in PR"

CATEGORIES = ("BUILT", "PARTIAL", "NOT BUILT", "POLICY", "SUPERSEDED")
"""The words a *Built?* cell of ``docs/adr-status.md`` starts with; a note may follow."""

_BUILD_STATUS = re.compile(r"^## Build status\n(.*?)(?=^## |\Z)", re.M | re.S)
_OVERVIEW_ROW = re.compile(r"^\| (\d{4}) \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$")


class TableError(ValueError):
    """A row uses a word outside the agreed vocabulary."""


@dataclass(frozen=True)
class Row:
    """One ADR's line in ``docs/adr-status.md``."""

    number: str
    title: str
    built: str
    evidence: str
    missing: str

    @property
    def category(self) -> str:
        """The word the *Built?* cell starts with.

        Raises:
            TableError: The cell starts with none of ``CATEGORIES``.
        """
        found = next((c for c in CATEGORIES if self.built.startswith(c)), None)
        if found is None:
            raise TableError(f"ADR {self.number} is {self.built!r}, none of {CATEGORIES}")
        return found


def overview_rows(text: str) -> list[Row]:
    """Return the ADR rows of ``docs/adr-status.md``, in file order."""
    return [Row(*m.groups()) for line in text.splitlines() if (m := _OVERVIEW_ROW.match(line))]


def table_rows(adr: str) -> list[list[str]] | None:
    """Return the cells of each row of an ADR's *Build status* table; ``None`` without one."""
    section = _BUILD_STATUS.search(adr)
    if section is None:
        return None
    rows = []
    for line in section.group(1).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 4 and cells[0] != "Step":
            if set(cells[0]) != {"-"}:
                rows.append(cells)
    return rows


def state_of(cells: list[str]) -> str:
    """Return the agreed word a row's State cell starts with.

    Raises:
        TableError: The cell starts with none of ``STATES``.
    """
    found = next((s for s in STATES if cells[-1].startswith(s)), None)
    if found is None:
        raise TableError(f"step {cells[0]}: state {cells[-1]!r} is none of {', '.join(STATES)}")
    return found


def contradicts(category: str, step: str, state: str) -> bool:
    """Return whether one *Build status* row contradicts the overview's word for its ADR.

    BUILT with a step still open (or waiting in a pull request), or NOT BUILT with a step whose
    code is in. Step 0, the ADR itself, never contradicts (ADR 0119).
    """
    if step == "0":
        return False
    if category == "BUILT":
        return state in OPEN_STATES | {IN_PR}
    return category == "NOT BUILT" and state in CODE_STATES


def drift_problem(category: str, rows: list[list[str]]) -> str | None:
    """Return how an overview category contradicts its ADR's table, if it does (ADR 0119).

    Args:
        category: The overview's word for the ADR.
        rows: The ADR's *Build status* rows.

    Returns:
        One sentence naming the contradicting steps, or ``None``.
    """
    steps = [cells[0] for cells in rows if contradicts(category, cells[0], state_of(cells))]
    if not steps:
        return None
    if category == "BUILT":
        return f"listed as BUILT but step {', '.join(steps)} is still open"
    return f"listed as NOT BUILT but step {', '.join(steps)} is merged"
