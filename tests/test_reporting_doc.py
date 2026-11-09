"""docs/reporting.md must equal what the code generates (ADR 0117 point 6)."""

from pathlib import Path

import pytest

from app.cli import main
from app.domain.usage import METRICS, FactKind
from app.web.reporting_doc import render_api_markdown, render_markdown

DOC = Path(__file__).resolve().parents[1] / "docs" / "reporting.md"


def test_published_metrics_match_the_catalogue() -> None:
    assert DOC.read_text() == render_markdown(), (
        "docs/reporting.md is out of date: run `python -m app.cli reporting-doc > "
        "docs/reporting.md`"
    )


def test_every_metric_and_fact_is_in_the_document() -> None:
    text = render_markdown()

    assert all(f"| {m.id} |" in text for m in METRICS)
    assert all(f"`{kind.value}`" in text for kind in FactKind)


def test_the_document_never_says_anonymised() -> None:
    """ADR 0102: a purge deletes fields; the statistics hold no personal data."""
    assert "anonymis" not in render_markdown().lower()


def test_reporting_doc_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reporting-doc"]) == 0

    assert capsys.readouterr().out == render_markdown()


API_DOC = Path(__file__).resolve().parents[1] / "docs" / "reporting-api.md"


def test_the_published_usage_api_matches_the_code() -> None:
    assert API_DOC.read_text() == render_api_markdown(), (
        "docs/reporting-api.md is out of date: run `python -m app.cli reporting-api-doc > "
        "docs/reporting-api.md`"
    )


def test_the_usage_api_document_lists_no_team_metric_and_the_command_prints_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = render_api_markdown()

    assert all(f"| {m.id} |" in text for m in METRICS if not m.staff)
    assert all(f"| {m.id} |" not in text for m in METRICS if m.staff)
    assert main(["reporting-api-doc"]) == 0
    assert capsys.readouterr().out == text
