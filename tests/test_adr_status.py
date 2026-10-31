"""``docs/adr-status.md`` lists every ADR with what runs, and the ADRs agree with it.

The product owner found decisions that were written down and never built, with nobody
remembering which. A listed ADR must not read as a built one (ADR 0059 point 7, CLAUDE.md):
every ADR is in the table, the counts add up, each ADR's Status line says what is built, and
every *Build status* table uses the agreed states.
"""

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs" / "adr"
STATUS_FILE = ROOT / "docs" / "adr-status.md"

CATEGORIES = ("BUILT", "PARTIAL", "NOT BUILT", "POLICY")
"""The words a *Built?* cell starts with; a note may follow in brackets."""

FIRST_ADR_WITH_BUILD_TABLE = 59
"""ADR 0059 point 7: from here on every ADR carries its own *Build status* table."""

STATES = ("planned", "in PR", "merged", "built", "live", "follow-up")
"""What a *Build status* row may start its state with (ADR 0059 point 7)."""

ROW = re.compile(r"^\| (\d{4}) \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$")


@dataclass(frozen=True)
class Row:
    """One ADR's line in the overview."""

    number: str
    title: str
    built: str
    evidence: str
    missing: str

    @property
    def category(self) -> str:
        """The word the *Built?* cell starts with."""
        return next(c for c in CATEGORIES if self.built.startswith(c))


def parse_rows(text: str) -> list[Row]:
    """Return the ADR rows of the overview table."""
    return [Row(*m.groups()) for line in text.splitlines() if (m := ROW.match(line.strip()))]


def status_paragraph(adr: Path) -> str:
    """Return the ADR's Status line with its continuation, lower case, on one line."""
    found = re.search(r"^Status:\s*(.*?)(?:\n\n|\Z)", adr.read_text(), re.M | re.S)
    assert found, f"{adr.name} has no Status line"
    return found.group(1).replace("\n", " ").lower()


def status_problem(row: Row, status: str) -> str | None:
    """Return what is wrong between an overview row and the ADR's Status line, if anything."""
    if row.category in ("BUILT", "POLICY") and status.startswith("proposed"):
        return "listed as built but its Status line says proposed"
    if row.category in ("BUILT", "POLICY") and ("partly built" in status or "not built" in status):
        return "listed as built but its Status line says it is not (fully) built"
    if row.category == "PARTIAL" and "partly" not in status:
        return "listed as partial but its Status line does not say partly built"
    if row.category == "NOT BUILT" and not ("not built" in status or status.startswith("proposed")):
        return "listed as not built but its Status line does not say so"
    return None


def build_status_rows(adr: Path) -> list[list[str]] | None:
    """Return the rows (cells) of the ADR's *Build status* table, or ``None`` without one."""
    found = re.search(r"^## Build status\n(.*?)(?=^## |\Z)", adr.read_text(), re.M | re.S)
    if found is None:
        return None
    rows = []
    for line in found.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if (
            line.startswith("|")
            and len(cells) >= 4
            and cells[0] != "Step"
            and set(cells[0]) != {"-"}
        ):
            rows.append(cells)
    return rows


def state_problem(cells: list[str]) -> str | None:
    """Return what is wrong with one *Build status* row, if anything."""
    state, pr = cells[-1], cells[-2]
    if not state.startswith(STATES):
        return f"state {state!r} is none of {', '.join(STATES)}"
    if state.startswith("in PR") and not re.search(r"#\d+", pr + state):
        return "state 'in PR' without the PR number"
    return None


ADRS = sorted(ADR_DIR.glob("0[0-9][0-9][0-9]-*.md"))
TEXT = STATUS_FILE.read_text()
ROWS = parse_rows(TEXT)


def test_there_are_adrs_and_rows() -> None:
    assert ADRS
    assert ROWS


def test_every_adr_is_listed_once_and_nothing_else() -> None:
    numbers = [row.number for row in ROWS]
    files = [adr.name[:4] for adr in ADRS]

    assert sorted(set(files) - set(numbers)) == [], "an ADR is missing from docs/adr-status.md"
    assert sorted(set(numbers) - set(files)) == [], "docs/adr-status.md lists an ADR that is gone"
    assert [n for n, c in Counter(numbers).items() if c > 1] == [], "an ADR is listed twice"


def test_the_overview_keeps_no_counts_of_its_own() -> None:
    """A hand-kept count table made every pull request that touched a row conflict."""
    assert not re.search(r"^\| (BUILT|PARTIAL|NOT BUILT|POLICY) \| \d+ \|$", TEXT, re.M)


def test_the_overview_says_when_it_was_checked() -> None:
    assert re.search(r"Checked against the code on \d{4}-\d{2}-\d{2}", TEXT)


def test_every_row_names_its_evidence_and_what_is_missing() -> None:
    for row in ROWS:
        assert row.evidence.strip(), row.number
        assert row.missing.strip(), row.number
        if row.category in ("PARTIAL", "NOT BUILT"):
            assert row.missing.strip() not in {"–", "-"}, f"{row.number}: say what is missing"


def test_each_status_line_agrees_with_the_overview() -> None:
    by_number = {adr.name[:4]: adr for adr in ADRS}
    problems = {
        row.number: problem
        for row in ROWS
        if (problem := status_problem(row, status_paragraph(by_number[row.number])))
    }

    assert problems == {}


def test_new_adrs_carry_a_build_status_table() -> None:
    missing = [
        adr.name
        for adr in ADRS
        if int(adr.name[:4]) >= FIRST_ADR_WITH_BUILD_TABLE and build_status_rows(adr) is None
    ]

    assert missing == []


def test_build_status_tables_use_the_agreed_states() -> None:
    problems = {}
    for adr in ADRS:
        for cells in build_status_rows(adr) or []:
            if problem := state_problem(cells):
                problems[f"{adr.name} step {cells[0]}"] = problem

    assert problems == {}


def test_the_checks_find_what_they_are_meant_to() -> None:
    built = Row("0001", "t", "BUILT", "e", "–")
    partial = Row("0002", "t", "PARTIAL", "e", "x")
    not_built = Row("0003", "t", "NOT BUILT (policy part)", "e", "x")

    assert status_problem(built, "proposed (2026-10-30)")
    assert status_problem(built, "accepted. partly built; see the table")
    assert status_problem(partial, "accepted (2026-10-30)")
    assert status_problem(not_built, "accepted (2026-10-30)")
    assert status_problem(built, "accepted (2026-10-30)") is None
    assert status_problem(partial, "accepted. partly built") is None
    assert status_problem(not_built, "proposed (2026-10-30)") is None
    assert state_problem(["1", "what", "#5", "done soon"])
    assert state_problem(["1", "what", "", "in PR"])
    assert state_problem(["1", "what", "#5", "in PR"]) is None
    assert state_problem(["1", "what", "", "planned"]) is None
    assert parse_rows("| 0001 | T | BUILT | e | – |") == [Row("0001", "T", "BUILT", "e", "–")]
