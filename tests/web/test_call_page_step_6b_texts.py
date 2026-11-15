"""The words of ADR 0123 step 6b that a reviewer asked to be exact.

The captions findings report the site's closed-captions option and never say that live captions
work in the call page (they are a different feature, open decision O8); a trial call has its own
words in the states where the appointment's would be wrong; and the privacy setting's label is
short (the long sentence lives in the manual and the registry).
"""

import pytest

from app.i18n import Translator, load_catalogues

TRANSLATOR = Translator(load_catalogues())
CAPTIONS = [
    "admin.check.webex.captions_ok",
    "admin.check.webex.captions_off",
    "admin.check.webex.captions_unknown",
    "admin.check.webex.captions_refused",
    "admin.check.webex.captions_left",
]
PARAMS = {"error": "rejected_400", "host": "host@example.nl", "meeting": "m-1"}


@pytest.mark.parametrize("key", CAPTIONS[:3])
def test_a_captions_finding_says_which_feature_it_reports(key: str) -> None:
    english = TRANSLATOR.gettext("en", key, **PARAMS)
    dutch = TRANSLATOR.gettext("nl", key, **PARAMS)

    assert "closed-captions option" in english
    assert "live captions" in english  # and what it does not say
    assert "ondertitelingsoptie" in dutch
    assert "live ondertiteling" in dutch


@pytest.mark.parametrize("key", CAPTIONS)
def test_every_captions_finding_has_both_languages(key: str) -> None:
    for locale in ("nl", "en"):
        text = TRANSLATOR.gettext(locale, key, **PARAMS)
        assert text != key
        assert "{" not in text


def test_the_dutch_ok_sentence_is_plain() -> None:
    text = TRANSLATOR.gettext("nl", "admin.check.webex.captions_ok")

    assert "houdt ondertitels aan" not in text
    assert text.startswith("De site van het videoplatform heeft de ondertitelingsoptie")


@pytest.mark.parametrize(
    ("trial", "appointment"),
    [
        ("trial.page.back", "join.back"),
        ("trial.page.not_open", "callpage.failed.not_open"),
        ("trial.page.left_body", "callpage.left.body"),
        ("trial.page.ended_body", "callpage.ended.body"),
        ("trial.page.ended_deleted", "callpage.ended.deleted"),
    ],
)
def test_a_trial_call_has_its_own_words_and_never_says_your_appointment(
    trial: str, appointment: str
) -> None:
    for locale in ("nl", "en"):
        own = TRANSLATOR.gettext(locale, trial)
        assert own != TRANSLATOR.gettext(locale, appointment)
        assert own != trial
    assert "appointment" not in TRANSLATOR.gettext("en", "trial.page.back").lower()
    assert "afspraak" not in TRANSLATOR.gettext("nl", "trial.page.back").lower()


def test_the_privacy_setting_label_is_a_label_and_not_a_paragraph() -> None:
    for locale in ("nl", "en"):
        label = TRANSLATOR.gettext(locale, "settings.field.privacy_statement_url.label")
        assert len(label) <= 70
        assert "." not in label
