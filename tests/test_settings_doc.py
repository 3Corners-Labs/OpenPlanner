"""docs/settings.md must equal what the code generates (ADR 0042 §3)."""

from pathlib import Path

from app.web.settings_doc import render_markdown

DOC = Path(__file__).resolve().parents[1] / "docs" / "settings.md"


def test_published_settings_match_the_registry() -> None:
    assert DOC.read_text() == render_markdown(), (
        "docs/settings.md is out of date: run `python -m app.cli settings-doc > docs/settings.md`"
    )
