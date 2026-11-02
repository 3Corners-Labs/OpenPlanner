"""docs/logging.md must equal what the register generates (ADR 0103 point 2)."""

from pathlib import Path

from app.web.logging_doc import render_markdown

DOC = Path(__file__).resolve().parents[1] / "docs" / "logging.md"


def test_the_logging_doc_matches_the_register() -> None:
    assert DOC.read_text() == render_markdown(), (
        "docs/logging.md is out of date: run `python -m app.cli logging-doc > docs/logging.md`"
    )
