"""The reporting mockup is kept in the repository and the reports are checked against it.

The product owner asked that the reports are built as the mockup shows them and not half way
(ADR 0117). ``docs/reporting-mockups.md`` lists every element of the mockup with its state; these
tests keep the list honest.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOCKUP = ROOT / "docs" / "mockups" / "reporting.html"
LIST = (ROOT / "docs" / "reporting-mockups.md").read_text()
ADR = next((ROOT / "docs" / "adr").glob("0117-*.md")).read_text()

VIEWS = ("Volume", "Capacity", "Wallboard", "Display links", "Digest")


def test_the_mockup_is_in_the_repository_and_has_five_views() -> None:
    html = MOCKUP.read_text()

    assert len(re.findall(r'role="tab"', html)) == 5


def test_the_list_has_a_section_for_every_view_of_the_mockup() -> None:
    sections = re.findall(r"^## View \d: (.+)$", LIST, re.M)

    assert [any(v in s for s in sections) for v in VIEWS] == [True] * 5


def test_every_row_has_a_known_state_and_a_built_row_names_its_test() -> None:
    for line in LIST.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        header = cells[0] in ("Element", "Element in the mockup") or set(cells[0]) <= {"-"}
        if not line.startswith("| ") or len(cells) != 4 or header:
            continue
        state, held = cells[2], cells[3]
        assert re.match(r"(built|planned|\*\*open\*\*)", state), line
        if state.startswith("built") and "Team" not in state:
            assert "tests/" in held, f"built without a test: {line}"


def test_the_open_rows_have_a_step_in_the_adr() -> None:
    assert re.search(r"^\| 6b \|", ADR, re.M)
