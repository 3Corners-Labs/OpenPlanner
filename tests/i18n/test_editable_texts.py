"""The allow-list of editable organisation texts matches the shipped catalogues (ADR 0004)."""

import pytest

from app.domain.text_overrides import EDITABLE_TEXTS, PAGE_TEXTS, EditableText
from app.i18n import CATALOGUE_DIR, load_catalogues, placeholders

CATALOGUES = load_catalogues(CATALOGUE_DIR)


@pytest.mark.parametrize("text", EDITABLE_TEXTS, ids=lambda t: t.key)
def test_every_editable_key_is_shipped_in_nl_and_en(text: EditableText) -> None:
    assert text.key in CATALOGUES["nl"]
    assert text.key in CATALOGUES["en"]


@pytest.mark.parametrize("text", PAGE_TEXTS, ids=lambda t: t.key)
def test_a_page_text_allows_exactly_the_placeholders_its_page_provides(text: EditableText) -> None:
    for locale in ("nl", "en"):
        assert placeholders(CATALOGUES[locale][text.key]) == text.placeholders


@pytest.mark.parametrize("text", EDITABLE_TEXTS, ids=lambda t: t.key)
def test_the_shipped_text_is_itself_a_valid_override(text: EditableText) -> None:
    for locale in ("nl", "en"):
        shipped = CATALOGUES[locale][text.key]
        assert placeholders(shipped) <= text.placeholders
        assert len(shipped) <= text.max_length
        assert not (text.single_line and "\n" in shipped)
