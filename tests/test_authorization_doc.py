"""docs/authorization.md must equal what the code generates (ADR 0014)."""

from pathlib import Path

import pytest

from app.i18n import Translator, load_catalogues
from app.web.authz_report import render_markdown
from tests.fakes.overrides import DictOverrides

DOC = Path(__file__).resolve().parents[1] / "docs" / "authorization.md"

# ADR 0069: the participant moves until the group's lead before the start, cancels until the start.
MOVE_UNTIL = {"nl": "minuten voor de start", "en": "minutes before the start"}
CANCEL_UNTIL = {"nl": "tot de start", "en": "until the start"}


def _participant_conditions(markdown: str) -> dict[str, str]:
    """Return the condition column of the participant table, by action key."""
    section = markdown.split("## Participants", 1)[1].split("\n## ", 1)[0]
    rows = [line.split("|") for line in section.splitlines() if "(`participant." in line]
    return {cells[1].split("`")[1]: cells[2].strip() for cells in rows}


def test_published_matrix_matches_the_code() -> None:
    generated = render_markdown(Translator(load_catalogues()))

    assert DOC.read_text() == generated, (
        "docs/authorization.md is out of date: run `python -m app.cli authz-matrix > "
        "docs/authorization.md`"
    )


@pytest.mark.parametrize("locale", ["nl", "en"])
def test_the_participant_table_gives_moving_and_cancelling_their_own_deadline(locale: str) -> None:
    conditions = _participant_conditions(render_markdown(Translator(load_catalogues()), locale))
    move = conditions["participant.booking_change"]
    cancel = conditions["participant.booking_cancel"]

    assert move != cancel
    assert MOVE_UNTIL[locale] in move
    assert CANCEL_UNTIL[locale] in cancel
    assert CANCEL_UNTIL[locale] not in move


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
