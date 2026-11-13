import gzip
import json
import re
from pathlib import Path

import pytest

# The words of ZAP's "Information Disclosure - Suspicious Comments" rule (10027). The baseline
# scan on main fails on any hit in a served script or page, and nothing in a PR runs it.
SUSPICIOUS = re.compile(
    r"\b(TODO|FIXME|BUGS?|XXX|QUERY|DB|ADMIN|ADMINISTRATOR|USER|USERNAME|SELECT|WHERE|FROM"
    r"|LATER|DEBUG)\b",
    re.IGNORECASE,
)
SCRIPT_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

STATIC = Path("app/web/static")
VENDOR = STATIC / "vendor"
"""Third-party files, one folder per library, each with a manifest (ADR 0118 point 6). They are
exempt on purpose from the 30 KB and 100 KB budgets, the markup-sink scan and the comment scan
below: the call page's SDK is 3.7 MB, has its own gate in ``tests/web/test_vendor.py`` and is
not ours to rewrite (ADR 0123 point 3). Every script of ours is still checked by all of them."""
SCRIPTS = sorted(path for path in STATIC.rglob("*.js") if VENDOR not in path.parents)
VENDORED = sorted(VENDOR.rglob("*.js"))
ISLANDS_DOC = Path("docs/islands.md")
MAX_SCRIPT_BYTES = 30 * 1024
MAX_ALL_SCRIPTS_BYTES = 100 * 1024
"""The gzipped size budgets of ADR 0118 point 7: fixed gates, not settings."""
MARKUP_SINKS = re.compile(
    r"\.(innerHTML|outerHTML|srcdoc)\s*\+?=(?!=)|insertAdjacentHTML\s*\(|createContextualFragment\s*\("
    r"|document\.write(ln)?\s*\(|\beval\s*\(|\bFunction\s*\("
)
"""Ways a script can turn text into markup or code (ADR 0118 point 9); mirrored in .semgrep."""
TEMPLATES = sorted(Path("app").rglob("*.html"))


def _hits(text: str, comment: re.Pattern[str]) -> list[str]:
    return [
        f"line {text.count(chr(10), 0, found.start()) + 1}: {word.group()}"
        for found in comment.finditer(text)
        for word in SUSPICIOUS.finditer(found.group())
    ]


def test_there_are_scripts_to_check() -> None:
    assert SCRIPTS


@pytest.mark.parametrize("path", SCRIPTS, ids=str)
def test_served_script_comments_pass_the_zap_baseline(path: Path) -> None:
    assert _hits(path.read_text(), SCRIPT_COMMENT) == []


@pytest.mark.parametrize("path", TEMPLATES, ids=str)
def test_html_comments_pass_the_zap_baseline(path: Path) -> None:
    # Jinja comments ({# #}) never reach the browser; HTML comments do.
    assert _hits(path.read_text(), HTML_COMMENT) == []


def test_the_check_finds_a_suspicious_word() -> None:
    assert _hits("/* text comes from the page */\nvar a; // ok", SCRIPT_COMMENT) == ["line 1: from"]


def _code_lines(text: str) -> str:
    """Return the lines that are not whole-line comments (a ``//`` inside a string stays code)."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("//", "/*", "*"))
    )


def _gzipped(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), mtime=0))


def _listed() -> set[str]:
    """Return the scripts that ``docs/islands.md`` has a ``### `name.js` `` entry for."""
    return set(re.findall(r"^### `([^`]+\.js)`", ISLANDS_DOC.read_text(), re.MULTILINE))


def test_the_islands_document_lists_every_script_and_only_those() -> None:
    present = {path.relative_to(STATIC).as_posix() for path in [*SCRIPTS, *VENDORED]}
    # A vendored file may be listed before it is placed (ADR 0123 point 3: not committed until
    # the licence answer), never a script of ours.
    expected_later = {
        f"vendor/{path.parent.name}/{json.loads(path.read_text())['file']}"
        for path in VENDOR.glob("*/manifest.json")
    }

    assert present - _listed() == set(), "add these scripts to docs/islands.md"
    assert _listed() - present - expected_later == set(), "docs/islands.md names a gone script"


@pytest.mark.parametrize("path", SCRIPTS, ids=str)
def test_a_script_stays_inside_its_size_budget(path: Path) -> None:
    assert _gzipped(path) <= MAX_SCRIPT_BYTES


def test_all_scripts_together_stay_inside_the_size_budget() -> None:
    assert sum(_gzipped(path) for path in SCRIPTS) <= MAX_ALL_SCRIPTS_BYTES


@pytest.mark.parametrize("path", SCRIPTS, ids=str)
def test_a_script_never_turns_text_into_markup_or_code(path: Path) -> None:
    found = MARKUP_SINKS.search(_code_lines(path.read_text()))

    assert found is None, f"{path}: {found.group(0)!r} (use textContent, ADR 0118 point 9)"


def test_the_markup_check_finds_each_sink() -> None:
    for sink in (
        "a.innerHTML = x",
        "a.outerHTML= x",
        "a.insertAdjacentHTML('x', y)",
        "document.write(x)",
        "eval(x)",
        "new Function('x')",
    ):
        assert MARKUP_SINKS.search(sink), sink
    assert MARKUP_SINKS.search("a.textContent = x") is None
    assert MARKUP_SINKS.search("if (a.innerHTML == b) {}") is None
    assert MARKUP_SINKS.search('var u = "http://x"; a.innerHTML = y;\n') is not None
