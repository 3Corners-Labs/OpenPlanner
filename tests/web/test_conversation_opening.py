"""The first message of a conversation task holds only what an outbound payload may (step 7a)."""

import pytest

from app.domain.calls import ContactCenterTask
from app.domain.contact_center import TaskKind
from app.domain.message_texts import CONVERSATION_OPENING_PLACEHOLDERS, used_placeholders
from app.i18n import CATALOGUE_DIR, Translator, load_catalogues
from app.web.conversation_opening import (
    OPENING_WITH_LINK,
    OPENING_WITHOUT_LINK,
    conversation_opening,
)
from tests.support.domain import webex_routing
from tests.support.locales import REQUIRED_LOCALES

TASK = ContactCenterTask(
    reference="OP-000123",
    first_name="Zoë",
    start_label="5 jan 2026, 09:00",
    group_name="Demo",
    meeting_link="https://site.webex.com/m/1",
    kind="scheduled",
    routing=webex_routing(),
    routing_value="demo",
    task_kind=TaskKind.CONVERSATION,
)


@pytest.fixture(scope="module")
def translator() -> Translator:
    return Translator(load_catalogues(CATALOGUE_DIR))


@pytest.mark.parametrize("locale", REQUIRED_LOCALES)
@pytest.mark.parametrize("key", [OPENING_WITH_LINK, OPENING_WITHOUT_LINK])
def test_the_text_uses_only_allowed_placeholders_in_every_locale(
    translator: Translator, locale: str, key: str
) -> None:
    names = used_placeholders(translator.lookup(locale, key))

    assert names is not None
    assert names <= CONVERSATION_OPENING_PLACEHOLDERS


@pytest.mark.parametrize("locale", REQUIRED_LOCALES)
def test_the_opening_names_the_appointment_and_the_link(
    translator: Translator, locale: str
) -> None:
    text = conversation_opening(translator, locale, TASK)

    for value in ("OP-000123", "Zoë", "5 jan 2026, 09:00", "Demo", "https://site.webex.com/m/1"):
        assert value in text
    assert "{" not in text


@pytest.mark.parametrize("locale", REQUIRED_LOCALES)
def test_a_conversation_without_a_meeting_has_no_link_in_its_opening(
    translator: Translator, locale: str
) -> None:
    text = conversation_opening(
        translator, locale, ContactCenterTask(**{**TASK.__dict__, "meeting_link": ""})
    )

    assert "OP-000123" in text
    assert "http" not in text


def test_the_two_languages_differ(translator: Translator) -> None:
    assert conversation_opening(translator, "nl", TASK) != conversation_opening(
        translator, "en", TASK
    )


def test_no_supplier_name_in_the_text(translator: Translator) -> None:
    for locale in REQUIRED_LOCALES:
        for key in (OPENING_WITH_LINK, OPENING_WITHOUT_LINK):
            assert "webex" not in translator.lookup(locale, key).lower()
