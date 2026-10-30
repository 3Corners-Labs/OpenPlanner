"""Editable organisation texts: allow-listed keys, placeholders and lengths (ADR 0004, 0009)."""

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.message_texts import BLOCKS_BY_KEY
from app.domain.text_overrides import (
    EDITABLE_BY_KEY,
    EDITABLE_TEXTS,
    PAGE_TEXTS,
    Area,
    clean_override,
)


def test_every_message_block_is_editable_with_its_own_allow_list() -> None:
    for key, block in BLOCKS_BY_KEY.items():
        text = EDITABLE_BY_KEY[key]
        assert (text.area, text.placeholders, text.max_length, text.single_line) == (
            Area.MESSAGES,
            block.placeholders,
            block.max_length,
            block.single_line,
        )


def test_keys_are_unique_and_page_texts_are_on_pages() -> None:
    keys = [text.key for text in EDITABLE_TEXTS]

    assert len(keys) == len(set(keys))
    assert {text.area for text in PAGE_TEXTS} == {Area.PAGES}
    assert len(EDITABLE_BY_KEY) == len(EDITABLE_TEXTS)


def test_a_page_text_may_use_the_placeholders_of_its_shipped_text() -> None:
    assert (
        clean_override("confirm.text", "  Bevestig voor {deadline}. ")
        == "Bevestig voor {deadline}."
    )
    assert clean_override("group.intro", "Een gesprek duurt {minutes} min.") == (
        "Een gesprek duurt {minutes} min."
    )
    assert clean_override("booked.text", "Zonder plaatsvervangers") == "Zonder plaatsvervangers"


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("home.title", "niet bewerkbaar"),  # not on the allow-list
        ("admin.title", "x"),
        ("booked.text", ""),
        ("booked.text", "   "),
        ("booked.text", "Hallo {first_name}"),  # not a placeholder of this text
        ("booked.text", "{group_name}"),
        ("confirm.text", "{deadline.__class__}"),
        ("confirm.text", "{deadline"),
        ("direct.card.title", "twee\nregels"),
        ("booked.text", "bel\x07"),
        ("booked.text", "x" * 2001),
        ("direct.card.title", "x" * 201),
    ],
)
def test_refused_texts(key: str, text: str) -> None:
    with pytest.raises(DomainError) as error:
        clean_override(key, text)

    assert error.value.code is ErrorCode.INVALID_CONFIGURATION
    assert error.value.field == ("key" if key not in EDITABLE_BY_KEY else key)


def test_markup_is_kept_as_plain_text() -> None:
    # Output is escaped (Jinja autoescape, plain-text mail part); the domain stores text as typed.
    assert clean_override("booked.text", "<b>vet</b>") == "<b>vet</b>"
