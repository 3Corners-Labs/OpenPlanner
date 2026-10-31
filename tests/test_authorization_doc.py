"""docs/authorization.md must equal what the code generates (ADR 0014)."""

from pathlib import Path

from app.i18n import Translator, load_catalogues
from app.web.authz_report import render_markdown
from tests.fakes.overrides import DictOverrides

DOC = Path(__file__).resolve().parents[1] / "docs" / "authorization.md"


def test_published_matrix_matches_the_code() -> None:
    generated = render_markdown(Translator(load_catalogues()))

    assert DOC.read_text() == generated, (
        "docs/authorization.md is out of date: run `python -m app.cli authz-matrix > "
        "docs/authorization.md`"
    )


def test_the_term_changes_no_rights_document() -> None:
    """The agent term is a label (ADR 0077): the generated matrix is the same with a term set."""
    plain = render_markdown(Translator(load_catalogues()))
    overrides = DictOverrides(
        {
            (1, "nl", "term.agent"): "behandelaar",
            (1, "nl", "term.agents"): "behandelaars",
            (1, "nl", "authz.role.org_admin"): "Hoofdbeheer",
            (1, "nl", "authz.role.group_admin"): "Teamleider",
        },
        group_texts={(1, "nl", "term.agent"): "agent", (1, "nl", "term.agents"): "agenten"},
    )

    with_term = render_markdown(Translator(load_catalogues(), overrides))

    assert with_term == plain
