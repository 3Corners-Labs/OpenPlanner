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

SCRIPTS = sorted(Path("app/web/static").glob("*.js"))
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
