"""docs/authorization.md must equal what the code generates (ADR 0014)."""

from pathlib import Path

from app.i18n import Translator, load_catalogues
from app.web.authz_report import render_markdown

DOC = Path(__file__).resolve().parents[1] / "docs" / "authorization.md"


def test_published_matrix_matches_the_code() -> None:
    generated = render_markdown(Translator(load_catalogues()))

    assert DOC.read_text() == generated, (
        "docs/authorization.md is out of date: run `python -m app.cli authz-matrix > "
        "docs/authorization.md`"
    )
