"""``docs/asvs.md`` names only things that exist, covers every audited requirement, and holds.

The document says, for each requirement the ASVS audit of 1 November 2026 judged partly
substantiated or a deviation, where the measure sits now. A source that was renamed or deleted
would leave a claim without proof, so this test checks:

* every file path and every ADR the document names exists;
* every audited requirement (pinned in ``tests/asvs_audited_ids.json``, so this test never reads
  the audit register, which lives outside the repository) has exactly one row with a status, a
  source and, for a limitation, the record of the decision;
* the summary counts match the rows;
* every outside-party requirement of ``tests/assurance_external_ids.json`` is listed under its
  checklist;
* the code facts the document relies on (V1.2.5, V1.2.9, V15.3.6) are asserted here, so they stay
  true.
"""

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "asvs.md").read_text()
AUDITED: dict[str, list[str]] = json.loads(
    (Path(__file__).parent / "asvs_audited_ids.json").read_text()
)
EXTERNAL: dict[str, list[str]] = json.loads(
    (Path(__file__).parent / "assurance_external_ids.json").read_text()
)

ID_SHAPE = re.compile(r"^(?:V\d+\.\d+\.\d+|TEN-\d+|PI-\d+)$")
STATUSES = ("Closed", "Limitation", "Open")
ROOTS = (
    "app",
    "docs",
    "tests",
    "scripts",
    "deploy",
    "e2e",
    "widget",
    "migrations",
    "site",
    ".github",
    ".semgrep",
)
TOP_LEVEL_FILES = {
    "Dockerfile",
    "Makefile",
    "docker-compose.yml",
    "renovate.json",
    "pyproject.toml",
    "uv.lock",
    "REUSE.toml",
    "CLAUDE.md",
    "README.md",
    "SECURITY.md",
}
PATH_TOKEN = re.compile(r"^[\w./-]+$")
RECORD_OF_A_LIMITATION = ("ADR", "docs/threat-model.md", "docs/cryptography.md")


def _tokens(text: str) -> list[str]:
    """Return the text between backticks."""
    return re.findall(r"`([^`\n]+)`", text)


def _is_path(token: str) -> bool:
    """Return whether a backticked token names a file or folder of the repository."""
    if not PATH_TOKEN.match(token):
        return False
    in_a_folder = "/" in token and token.split("/", 1)[0] in ROOTS
    return in_a_folder or token in TOP_LEVEL_FILES


def _rows() -> dict[str, list[str]]:
    """Return the cells of every requirement row: ID, what was asked, the measure, the status."""
    rows: dict[str, list[str]] = {}
    for line in DOC.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if ID_SHAPE.match(cells[0]):
            assert cells[0] not in rows, f"{cells[0]} has two rows"
            rows[cells[0]] = cells
    return rows


ROWS = _rows()


def _expand(text: str) -> set[str]:
    """Return the requirement IDs in ``text``, with ``V6.2.1 to V6.2.12`` expanded to every ID."""
    found = set(re.findall(r"(?:V\d+\.\d+\.\d+|TEN-\d+|PI-\d+)", text))
    for chapter, section, first, last in re.findall(
        r"V(\d+)\.(\d+)\.(\d+) to V\1\.\2\.(\d+)", text
    ):
        found.update(f"V{chapter}.{section}.{n}" for n in range(int(first), int(last) + 1))
    return found


def test_every_path_the_document_names_exists() -> None:
    """A path in backticks is a file or folder of the repository."""
    paths = {token for token in _tokens(DOC) if _is_path(token)}

    assert len(paths) > 100
    assert sorted(path for path in paths if not (ROOT / path).exists()) == []


def test_every_adr_the_document_names_exists() -> None:
    """``ADR 0091`` is a file in ``docs/adr/``."""
    numbers = set(re.findall(r"ADR (\d{4})", DOC))

    assert numbers
    missing = [n for n in sorted(numbers) if not list((ROOT / "docs" / "adr").glob(f"{n}-*.md"))]
    assert missing == []


def test_every_audited_requirement_has_exactly_one_row() -> None:
    """The rows are the pinned list, no more and no less."""
    pinned = [*AUDITED["asvs"], *AUDITED["further"]]

    assert len(pinned) == len(set(pinned)) == 99
    assert sorted(ROWS) == sorted(pinned)


@pytest.mark.parametrize("requirement", sorted(ROWS))
def test_a_row_has_a_status_and_a_source(requirement: str) -> None:
    """Four cells, a status word, and at least one path in the measure cell."""
    cells = ROWS[requirement]

    assert len(cells) == 4, requirement
    assert all(cells), f"{requirement} has an empty cell"
    assert cells[3] in STATUSES, f"{requirement}: {cells[3]!r}"
    assert any(_is_path(token) for token in _tokens(cells[2])), f"{requirement} names no file"


def test_a_limitation_names_the_record_of_the_decision() -> None:
    """A limitation is on purpose, so an ADR or the threat model must say so."""
    for requirement, cells in ROWS.items():
        if cells[3] == "Limitation":
            assert any(record in cells[2] for record in RECORD_OF_A_LIMITATION), requirement


def test_the_summary_matches_the_rows() -> None:
    """The counts in the summary table are the counts of the rows."""
    found = re.search(r"### Summary\n(.*?)\n\*\*Limitation", DOC, re.S)
    assert found
    summary: dict[str, tuple[int, int]] = {}
    for line in found.group(1).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] in (*STATUSES, "Total"):
            summary[cells[0]] = (int(cells[1]), int(cells[2]))
    expected = {
        status: (
            sum(1 for r in AUDITED["asvs"] if ROWS[r][3] == status),
            sum(1 for r in AUDITED["further"] if ROWS[r][3] == status),
        )
        for status in STATUSES
    }
    expected["Total"] = (len(AUDITED["asvs"]), len(AUDITED["further"]))

    assert summary == expected


def test_the_lists_of_limitations_and_open_rows_match_the_rows() -> None:
    """The two sentences under the summary name the same IDs as the rows."""
    for word, status in (("Limitation", "Limitation"), ("Open", "Open")):
        found = re.search(rf"\*\*{word}\*\* \(\d+\):(.*?)(?:\n\n)", DOC, re.S)
        assert found, word
        named = {i for i in _expand(found.group(1)) if i.startswith("V")}
        rows = {r for r, cells in ROWS.items() if cells[3] == status and r.startswith("V")}
        assert named == rows, word


def test_every_outside_requirement_is_listed_under_its_checklist() -> None:
    """The section for outside evidence names each pinned ID in the row of its own checklist."""
    section = DOC.split("## Evidence that only an outside party can give", 1)[1]
    by_checklist = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0].startswith("`docs/assurance/"):
            by_checklist[cells[0].strip("`").rsplit("/", 1)[1]] = _expand(cells[1])

    assert sorted(by_checklist) == sorted(EXTERNAL)
    for checklist, ids in EXTERNAL.items():
        assert by_checklist[checklist] == set(ids), checklist


def test_audited_and_outside_requirements_do_not_overlap() -> None:
    """A requirement is either in the rows above or in a checklist."""
    outside = {requirement for ids in EXTERNAL.values() for requirement in ids}

    assert outside & set(ROWS) == set()


# V1.2.5, V1.2.9: facts about the code that the document states.

PROCESS_CALLS = {
    "system",
    "popen",
    "spawn",
    "spawnl",
    "spawnv",
    "execv",
    "execl",
    "execvp",
    "execlp",
}
DANGEROUS_BUILTINS = {"eval", "exec"}
REGEX_FUNCTIONS = {
    "compile",
    "match",
    "search",
    "fullmatch",
    "sub",
    "subn",
    "findall",
    "finditer",
    "split",
}
APP_FILES = sorted((ROOT / "app").rglob("*.py"))


def _is_constant_pattern(node: ast.expr) -> bool:
    """Return whether a regex argument is a literal, an f-string over names or a constant."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.JoinedStr):
        return all(
            isinstance(part, ast.Constant)
            or (isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name))
            for part in node.values
        )
    return isinstance(node, ast.Name) and node.id.isupper()


def test_app_starts_no_process_and_runs_no_dynamic_code() -> None:
    """V1.2.5: no ``subprocess``, no ``os.system``, no ``eval``, no shell in ``app/``."""
    problems = []
    for path in APP_FILES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                a.name.split(".")[0] == "subprocess" for a in node.names
            ):
                problems.append(f"{path}: import subprocess")
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == "subprocess"
            ):
                problems.append(f"{path}: from subprocess")
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in DANGEROUS_BUILTINS:
                    problems.append(f"{path}:{node.lineno}: {func.id}()")
                if isinstance(func, ast.Attribute):
                    if (
                        isinstance(func.value, ast.Name)
                        and func.value.id == "os"
                        and func.attr in PROCESS_CALLS
                    ):
                        problems.append(f"{path}:{node.lineno}: os.{func.attr}()")
                    if func.attr.startswith("create_subprocess"):
                        problems.append(f"{path}:{node.lineno}: {func.attr}()")

    assert problems == []


def test_every_regular_expression_is_static() -> None:
    """V1.2.9: a pattern is never built from a request value, only from literals and constants."""
    problems = []
    for path in APP_FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"
                and node.func.attr in REGEX_FUNCTIONS
                and node.args
                and not _is_constant_pattern(node.args[0])
            ):
                problems.append(f"{path}:{node.lineno}: {ast.unparse(node)[:80]}")

    assert problems == []


# V15.3.6: the review of the widget stays valid while these constructs stay out.

WIDGET_FORBIDDEN = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
    "__proto__",
    "Object.assign",
    "setPrototypeOf",
)
JS_COMMENTS = re.compile(r"/\*.*?\*/|^\s*//.*$", re.S | re.M)


def test_the_widget_has_none_of_the_constructs_behind_prototype_pollution_or_injection() -> None:
    """V15.3.6: no markup from strings, no dynamic code, no merge of server data into objects."""
    files = sorted((ROOT / "widget" / "src").glob("*.js"))
    assert files
    found: list[str] = []
    for path in files:
        code = JS_COMMENTS.sub("", path.read_text())
        found.extend(f"{path.name}: {needle}" for needle in WIDGET_FORBIDDEN if needle in code)

    assert found == []


def test_the_widget_has_no_runtime_dependency() -> None:
    """V15.3.6: the widget bundles no library, so no library can bring a vulnerable merge helper."""
    package = json.loads((ROOT / "widget" / "package.json").read_text())

    assert "dependencies" not in package or package["dependencies"] == {}
