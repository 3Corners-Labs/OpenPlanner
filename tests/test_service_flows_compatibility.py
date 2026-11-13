"""The compatibility proof and the gate checklist of ADR 0121 step 8 stay true to the code.

``docs/service-flows-compatibility.md`` names a test for every cell of the compatibility matrix
and of the upgrade proof; ``docs/service-flows-gate-checklist.md`` lists what must be done before
``ROLLBACK_SAFE`` opens. These tests fail when a named test disappears, when an old behaviour
loses its section, when an open row of ADR 0121 or ADR 0122 has no checklist item, and when the
gate is opened while a blocking item is still open.
"""

import ast
import re
from collections import Counter
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest

from app.domain import service_flows
from scripts.adr_tables import OPEN_STATES, state_of, table_rows

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "service-flows-compatibility.md"
CHECKLIST = ROOT / "docs" / "service-flows-gate-checklist.md"
ADRS = {
    "0121": ROOT / "docs" / "adr" / "0121-services-flows-and-skills.md",
    "0122": ROOT / "docs" / "adr" / "0122-capacity-of-a-shared-pool.md",
}
OLD_BEHAVIOURS = (
    "Group booking",
    "Direct call",
    "Trial call",
    "Contact-center dispatch",
    "Emergency closure",
    "Rebooking and proposals",
    "Deletion of personal data",
    "Configuration export and import",
    "Public API `/api/v1`",
    "Agent API",
)
"""What OpenPlanner did before the train; each must have its own section of the matrix."""
STEP_8 = "8"
"""This step's own row: it is the proof, not an item of the checklist."""
TEST_REFERENCE = re.compile(r"`(tests/[\w/]+\.py)::(\w+)`")
ACCEPTANCE = [f"A{n:02d}" for n in range(1, 37)]
"""The rows of the acceptance matrix of ``docs/service-flows-handover.md`` (gate item G09)."""
GATE_OPENERS = ("open_gate", "ROLLBACK_SAFE")
ITEM = re.compile(r"\bG\d{2}\b")


@cache
def _functions(path: str) -> frozenset[str]:
    """Return the names of the module-level functions of the test file at ``path``."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return frozenset(
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )


def _table_rows(text: str) -> list[list[str]]:
    """Return the cells of every table row of a Markdown text, header and rule lines left out."""
    rows = []
    for line in text.splitlines():
        if not line.startswith("| ") or set(line) <= {"|", "-", " "}:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] not in {"Change", "Claim", "Item", "Row", "Step"}:
            rows.append(cells)
    return rows


def _sections(text: str) -> dict[str, str]:
    """Return the text under every ``###`` heading."""
    parts = re.split(r"^### (.+)$", text, flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


class Item(NamedTuple):
    """One row of the gate checklist."""

    item: str
    what: str
    source: str
    blocks: str
    owner: str
    status: str


def _checklist() -> dict[str, Item]:
    """Return the checklist's rows by item."""
    rows = _table_rows(CHECKLIST.read_text(encoding="utf-8"))
    return {cells[0]: Item(*cells) for cells in rows}


def _documents() -> str:
    """Return the text of both documents."""
    return MATRIX.read_text(encoding="utf-8") + CHECKLIST.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("path", "name"), sorted(set(TEST_REFERENCE.findall(_documents()))), ids=str
)
def test_every_test_the_documents_name_exists(path: str, name: str) -> None:
    assert (ROOT / path).is_file(), path
    assert name in _functions(path), f"{path}::{name}"


def test_every_old_behaviour_has_a_section_whose_rows_each_have_a_proof() -> None:
    sections = _sections(MATRIX.read_text(encoding="utf-8"))
    items = _checklist()

    for behaviour in OLD_BEHAVIOURS:
        heading = next((h for h in sections if h.startswith(behaviour)), None)
        assert heading is not None, behaviour
        rows = _table_rows(sections[heading])
        assert rows, behaviour
        for cells in rows:
            proof = cells[-1]
            named = TEST_REFERENCE.search(proof) or any(i in items for i in ITEM.findall(proof))
            assert named, f"{behaviour}: {cells}"


def test_every_open_row_of_both_adrs_has_a_checklist_item() -> None:
    wanted: Counter[str] = Counter()
    for number, path in ADRS.items():
        for cells in table_rows(path.read_text(encoding="utf-8")) or []:
            if state_of(cells) in OPEN_STATES and cells[0] != STEP_8:
                wanted[f"{number}/{cells[0]}"] += 1
    listed = Counter(
        item.source
        for item in _checklist().values()
        if "/" in item.source and not item.status.startswith("done")
    )

    assert wanted
    assert listed == wanted


def test_every_item_says_whether_it_blocks_who_owns_it_and_where_it_stands() -> None:
    for name, item in _checklist().items():
        assert item.blocks in {"yes", "cc", "no"}, name
        assert set(item.owner.split(", ")) <= {"build", "po", "release", "operator"}, name
        assert item.status.split(" ")[0] in {"open", "planned", "follow-up", "done"}, name


def test_the_gate_stays_shut_while_a_blocking_item_is_open() -> None:
    open_blocking = [
        name
        for name, item in _checklist().items()
        if item.blocks == "yes" and not item.status.startswith("done")
    ]

    if open_blocking:
        assert service_flows.ROLLBACK_SAFE is False, open_blocking


def _acceptance() -> dict[str, list[str]]:
    """Return the cells of the acceptance section's rows by row."""
    text = MATRIX.read_text(encoding="utf-8").split("## 4. Acceptance matrix", 1)[1]
    return {cells[0]: cells for cells in _table_rows(text) if cells[0].startswith("A")}


def test_every_acceptance_row_names_its_test_and_how_it_meets_the_gate() -> None:
    rows = _acceptance()

    assert list(rows) == ACCEPTANCE
    for name, (_row, _scenario, gate, proof) in rows.items():
        assert gate in {"open", "both", "n/a"}, name
        assert TEST_REFERENCE.search(proof), name


def test_a_row_that_runs_with_the_gate_open_names_a_module_that_opens_it() -> None:
    for name, (_row, _scenario, gate, proof) in _acceptance().items():
        if gate not in {"open", "both"}:
            continue
        path, _test = TEST_REFERENCE.findall(proof)[0]
        text = (ROOT / path).read_text(encoding="utf-8")
        assert any(opener in text for opener in GATE_OPENERS), f"{name}: {path}"
