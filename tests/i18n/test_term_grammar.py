"""A text never puts an article in front of the agent term (ADR 0077 point 9).

An organisation may choose any word for the person who takes the call: "behandelaar" is a
*de*-word, "teamlid" a *het*-word, and an English word may start with a vowel. So the shipped
texts say "een {agent}" (right for every Dutch word), "uw {agent}", "one of our {agents}" or
"our {agent}", and never "de {agent}", "het {agent}", "a {agent}" or "an {agent}".
"""

import re

import pytest

from app.i18n import CATALOGUE_DIR, load_catalogues

CATALOGUES = load_catalogues(CATALOGUE_DIR)
TERM = r"\{[Aa]gents?\}"
ARTICLES = {
    "nl": re.compile(rf"\b(de|het|die|dat|deze|dit)\s+{TERM}", re.IGNORECASE),
    "en": re.compile(rf"\b(a|an)\s+{TERM}", re.IGNORECASE),
}


@pytest.mark.parametrize("locale", sorted(ARTICLES))
def test_no_text_puts_an_article_before_the_agent_term(locale: str) -> None:
    found = {key: text for key, text in CATALOGUES[locale].items() if ARTICLES[locale].search(text)}

    assert found == {}


@pytest.mark.parametrize("locale", sorted(ARTICLES))
def test_the_guard_sees_an_article(locale: str) -> None:
    bad = {"nl": "De {agent} belt u.", "en": "An {agent} calls you."}[locale]

    assert ARTICLES[locale].search(bad)


@pytest.mark.parametrize("locale", ["nl", "en"])
def test_the_texts_that_name_the_term_use_it_in_both_locales(locale: str) -> None:
    keys = {k for k, text in CATALOGUES[locale].items() if "agent" in text.lower() and "{" in text}

    assert {"wait.searching", "direct.intro", "message.confirmation.intro"} <= keys
