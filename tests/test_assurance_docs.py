"""Every external requirement is in exactly one assurance checklist, with its evidence and owner.

``docs/assurance/`` turns the requirements that only an outside party can prove (identity
provider, host, Cisco, data controller) into checklists. The list of those requirements is
pinned in ``tests/assurance_external_ids.json`` so this test never reads the audit register,
which lives outside the repository. A new external requirement is added to the JSON and to its
checklist in the same pull request.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSURANCE = ROOT / "docs" / "assurance"
PINNED: dict[str, list[str]] = json.loads(
    (Path(__file__).parent / "assurance_external_ids.json").read_text()
)
CHECKLISTS = sorted(PINNED)
ID_SHAPE = re.compile(r"^(?:V\d+\.\d+\.\d+|TEN-\d+|PI-\d+)$")
CELLS_PER_ROW = 5
"""ID, what is asked, how to show it, owner, reference."""


def _mentions(text: str, requirement: str) -> bool:
    """Return whether ``requirement`` stands in ``text`` as a whole ID (V6.2.1 is not V6.2.10)."""
    pattern = rf"(?<![\w.-]){re.escape(requirement)}(?!\d|\.\d)"
    return re.search(pattern, text) is not None


def _rows(name: str) -> dict[str, list[str]]:
    """Return the cells of every table row of a checklist whose first cell is a requirement ID."""
    rows: dict[str, list[str]] = {}
    for line in (ASSURANCE / name).read_text().splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if ID_SHAPE.match(cells[0]):
            rows[cells[0]] = cells
    return rows


def test_the_pinned_list_has_no_duplicates() -> None:
    """One requirement has one home, also in the pinned list."""
    everything = [requirement for ids in PINNED.values() for requirement in ids]
    assert len(everything) == len(set(everything))
    assert all(ID_SHAPE.match(requirement) for requirement in everything)


def test_the_package_has_its_files() -> None:
    """The checklists, the pentest scope and the guide are all there, and the guide names them."""
    guide = (ASSURANCE / "README.md").read_text()
    for name in [*CHECKLISTS, "pentest-scope.md"]:
        assert (ASSURANCE / name).is_file(), name
        assert f"`{name}`" in guide, f"README.md does not name {name}"


@pytest.mark.parametrize("name", CHECKLISTS)
def test_a_checklist_holds_its_own_requirements(name: str) -> None:
    """Each pinned ID has a complete row in its checklist, and the checklist has no other ID."""
    rows = _rows(name)
    assert sorted(rows) == sorted(PINNED[name])
    for requirement, cells in rows.items():
        assert len(cells) == CELLS_PER_ROW, requirement
        assert all(cells), f"{requirement} has an empty cell"


@pytest.mark.parametrize("name", CHECKLISTS)
def test_a_requirement_is_in_no_other_checklist(name: str) -> None:
    """The ID of a requirement is not mentioned in any checklist but its own."""
    others = [other for other in CHECKLISTS if other != name]
    for requirement in PINNED[name]:
        for other in others:
            text = (ASSURANCE / other).read_text()
            assert not _mentions(text, requirement), f"{requirement} also in {other}"


def test_every_pinned_requirement_is_found_in_exactly_one_checklist() -> None:
    """Counting over all checklists: each pinned ID is in one file, no more and no less."""
    texts = {name: (ASSURANCE / name).read_text() for name in CHECKLISTS}
    for ids in PINNED.values():
        for requirement in ids:
            homes = [name for name, text in texts.items() if _mentions(text, requirement)]
            assert len(homes) == 1, f"{requirement} is in {homes}"


def test_the_pentest_scope_names_its_topics() -> None:
    """The scope covers smuggling, sessions, the issuer, tenants, the token exchange, the trial."""
    text = (ASSURANCE / "pentest-scope.md").read_text()
    for needle in (
        "V4.2.1",
        "ADR 0098",
        "ADR 0097",
        "ADR 0093",
        "Tenant boundaries",
        "tunnel trial",
    ):
        assert needle in text, needle


def test_the_documents_that_matter_link_to_the_package() -> None:
    """The README and the runbook point to the assurance package."""
    for name in ("README.md", "docs/runbook.md"):
        assert "docs/assurance/" in (ROOT / name).read_text(), name
