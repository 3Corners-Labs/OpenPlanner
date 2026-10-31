"""The agent term as placeholders of a text (ADR 0077)."""

from app.domain.agent_term import TERM_KEYS, TERM_PLACEHOLDERS
from app.i18n.formatting import format_message, placeholders
from app.i18n.terms import PLURAL_KEY, SINGULAR_KEY, AgentTermPlaceholders, capitalise
from app.i18n.translator import Translator
from tests.fakes.overrides import DictOverrides

CATALOGUES = {
    "nl": {
        "term.agent": "medewerker",
        "term.agents": "medewerkers",
        "line": "Een {agent} belt u. {Agents} zijn bereikbaar; geen {agents} vrij? Dan {agent}.",
        "label": "{Agent}",
        "explicit": "{agent}",
    },
    "en": {"term.agent": "agent", "term.agents": "agents", "line": "An {agent} calls."},
}


def test_the_keys_and_the_placeholders_equal_those_of_the_domain() -> None:
    assert (SINGULAR_KEY, PLURAL_KEY) == TERM_KEYS
    assert AgentTermPlaceholders.names == TERM_PLACEHOLDERS


def test_capitalise_changes_only_the_first_letter() -> None:
    assert capitalise("behandelaar") == "Behandelaar"
    assert capitalise("teamLid") == "TeamLid"
    assert capitalise("") == ""


def test_a_placeholder_may_start_with_a_capital() -> None:
    assert placeholders("{Agent} en {agents}") == {"Agent", "agents"}
    assert format_message("{Agent}", {"Agent": "Behandelaar"}) == "Behandelaar"


def test_the_shipped_words_fill_the_four_placeholders() -> None:
    translator = Translator(CATALOGUES)

    assert translator.gettext("nl", "line") == (
        "Een medewerker belt u. Medewerkers zijn bereikbaar; geen medewerkers vrij? Dan medewerker."
    )
    assert translator.gettext("en", "line") == "An agent calls."


def test_the_organisation_term_replaces_the_shipped_words() -> None:
    overrides = DictOverrides(
        {(7, "nl", "term.agent"): "behandelaar", (7, "nl", "term.agents"): "behandelaars"}
    )
    translator = Translator(CATALOGUES, overrides)

    assert translator.gettext("nl", "label", organisation_id=7) == "Behandelaar"
    assert translator.gettext("nl", "label", organisation_id=8) == "Medewerker"
    assert translator.gettext("nl", "label") == "Medewerker"


def test_the_group_term_beats_the_organisation_term() -> None:
    overrides = DictOverrides(
        {(7, "nl", "term.agent"): "behandelaar", (7, "nl", "term.agents"): "behandelaars"},
        group_texts={(3, "nl", "term.agent"): "agent", (3, "nl", "term.agents"): "agenten"},
    )
    translator = Translator(CATALOGUES, overrides)

    assert translator.gettext("nl", "label", organisation_id=7, group_id=3) == "Agent"
    assert translator.gettext("nl", "label", organisation_id=7, group_id=4) == "Behandelaar"
    assert translator.bind("nl", 7, 3)("label") == "Agent"


def test_a_value_of_the_caller_wins_over_the_term() -> None:
    translator = Translator(CATALOGUES)

    assert translator.gettext("nl", "explicit", agent="collega") == "collega"


def test_the_term_is_not_looked_up_for_a_text_without_it() -> None:
    translator = Translator({"nl": {"plain": "Hallo"}})

    assert translator.gettext("nl", "plain") == "Hallo"
