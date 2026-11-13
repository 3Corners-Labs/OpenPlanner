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

import pytest

from app.domain import service_flows

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
OPEN_STATES = {"planned", "follow-up"}
STEP_8 = "8"
"""This step's own row: it is the proof, not an item of the checklist."""
TEST_REFERENCE = re.compile(r"`(tests/[\w/]+\.py)::(\w+)`")
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
        if cells[0] not in {"Change", "Claim", "Item", "Step"}:
            rows.append(cells)
    return rows


def _sections(text: str) -> dict[str, str]:
    """Return the text under every ``###`` heading."""
    parts = re.split(r"^### (.+)$", text, flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _checklist() -> dict[str, list[str]]:
    """Return the checklist's rows by item."""
    return {cells[0]: cells for cells in _table_rows(CHECKLIST.read_text(encoding="utf-8"))}


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
        status = path.read_text(encoding="utf-8").split("## Build status", 1)[1]
        status = status.split("\n## ", 1)[0]
        for cells in _table_rows(status):
            step, state = cells[0], cells[-1]
            if state in OPEN_STATES and step != STEP_8:
                wanted[f"{number}/{step}"] += 1
    listed = Counter(cells[2] for cells in _checklist().values() if "/" in cells[2])

    assert wanted
    assert listed == wanted


def test_every_item_says_whether_it_blocks_who_owns_it_and_where_it_stands() -> None:
    for item, cells in _checklist().items():
        _, _, _, blocks, owner, status = cells
        assert blocks in {"yes", "cc", "no"}, item
        assert set(owner.split(", ")) <= {"build", "po", "release", "operator"}, item
        assert status.split(" ")[0] in {"open", "planned", "follow-up", "done"}, item


def test_the_gate_stays_shut_while_a_blocking_item_is_open() -> None:
    open_blocking = [
        item
        for item, cells in _checklist().items()
        if cells[3] in {"yes", "cc"} and not cells[5].startswith("done")
    ]

    assert open_blocking  # today: the gate has work before it
    assert service_flows.ROLLBACK_SAFE is False
