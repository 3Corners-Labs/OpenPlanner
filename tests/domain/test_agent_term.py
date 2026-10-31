"""The agent term: the word for the person who takes the call, and the rules for it (ADR 0077)."""

import pytest

from app.domain.agent_term import (
    MAX_TERM_LENGTH,
    TERM_PLURAL,
    TERM_SINGULAR,
    check_term_pair,
    clean_term,
)
from app.domain.errors import DomainError, ErrorCode


def test_a_word_is_trimmed() -> None:
    assert clean_term(TERM_SINGULAR, "  behandelaar ") == "behandelaar"


@pytest.mark.parametrize(
    "text",
    ["", "   ", "a", "x" * (MAX_TERM_LENGTH + 1), "twee\nregels", "met {agent}", "bel\x07"],
)
def test_a_word_that_breaks_a_rule_is_refused_with_its_key(text: str) -> None:
    with pytest.raises(DomainError) as refused:
        clean_term(TERM_PLURAL, text)

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION
    assert refused.value.field == TERM_PLURAL


def test_a_word_of_the_largest_size_is_allowed() -> None:
    assert clean_term(TERM_SINGULAR, "x" * MAX_TERM_LENGTH) == "x" * MAX_TERM_LENGTH


def test_only_the_two_term_keys_hold_a_word() -> None:
    with pytest.raises(DomainError) as refused:
        clean_term("group.intro", "behandelaar")

    assert refused.value.field == "key"


def test_the_singular_and_the_plural_change_together() -> None:
    check_term_pair({TERM_SINGULAR: "behandelaar", TERM_PLURAL: "behandelaars"})
    check_term_pair({TERM_SINGULAR: None, TERM_PLURAL: None})
    check_term_pair({})
    check_term_pair({TERM_SINGULAR: "behandelaar"})  # a form without the plural field


@pytest.mark.parametrize(
    ("words", "missing"),
    [
        ({TERM_SINGULAR: "behandelaar", TERM_PLURAL: None}, TERM_PLURAL),
        ({TERM_SINGULAR: None, TERM_PLURAL: "behandelaars"}, TERM_SINGULAR),
    ],
)
def test_a_form_that_sets_only_one_word_names_the_missing_one(
    words: dict[str, str | None], missing: str
) -> None:
    with pytest.raises(DomainError) as refused:
        check_term_pair(words)

    assert refused.value.field == missing
