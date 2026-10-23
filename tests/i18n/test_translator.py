import io
import json
import logging

import pytest
import structlog

from app.i18n.translator import Translator
from app.logging import configure_logging
from tests.fakes.overrides import DictOverrides

CATALOGS = {
    "nl": {"greeting": "Hallo {name}", "only.nl": "Alleen NL"},
    "en": {"greeting": "Hello {name}"},
}


@pytest.fixture
def log_stream() -> io.StringIO:
    out = io.StringIO()
    configure_logging("DEBUG", stream=out)
    yield out
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def test_requested_locale_from_catalogue() -> None:
    assert Translator(CATALOGS).gettext("en", "greeting", name="Ada") == "Hello Ada"


def test_falls_back_to_default_locale() -> None:
    assert Translator(CATALOGS).gettext("en", "only.nl") == "Alleen NL"


def test_unknown_locale_falls_back_to_default() -> None:
    assert Translator(CATALOGS).gettext("de", "greeting", name="Ada") == "Hallo Ada"


def test_missing_key_returns_key_and_logs_once(log_stream: io.StringIO) -> None:
    translator = Translator(CATALOGS)

    assert translator.gettext("en", "no.such.key") == "no.such.key"
    translator.gettext("en", "no.such.key")

    records = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    assert records == [
        {
            "event": "i18n.missing_key",
            "key": "no.such.key",
            "locale": "en",
            "level": "warning",
            "timestamp": records[0]["timestamp"],
        }
    ]


def test_global_override_beats_catalogue() -> None:
    overrides = DictOverrides({(None, "en", "greeting"): "Hi {name}"})

    assert Translator(CATALOGS, overrides).gettext("en", "greeting", name="Ada") == "Hi Ada"


def test_organisation_override_beats_global_override() -> None:
    overrides = DictOverrides(
        {(None, "en", "greeting"): "Hi {name}", (7, "en", "greeting"): "Welcome {name}"}
    )
    translator = Translator(CATALOGS, overrides)

    assert translator.gettext("en", "greeting", organisation_id=7, name="Ada") == "Welcome Ada"
    assert translator.gettext("en", "greeting", organisation_id=8, name="Ada") == "Hi Ada"


def test_override_in_requested_locale_beats_default_catalogue() -> None:
    overrides = DictOverrides({(None, "en", "only.nl"): "Only EN"})

    assert Translator(CATALOGS, overrides).gettext("en", "only.nl") == "Only EN"


def test_bind_returns_a_gettext_for_one_locale() -> None:
    _ = Translator(CATALOGS).bind("en")

    assert _("greeting", name="Ada") == "Hello Ada"
    assert _.locale == "en"


def test_bind_applies_organisation_overrides() -> None:
    overrides = DictOverrides({(7, "en", "greeting"): "Welcome {name}"})

    _ = Translator(CATALOGS, overrides).bind("en", organisation_id=7)

    assert _("greeting", name="Ada") == "Welcome Ada"


def test_locales_lists_catalogues_default_first() -> None:
    assert Translator({"en": {}, "de": {}, "nl": {}}).locales == ("nl", "de", "en")


def test_default_locale_catalogue_is_required() -> None:
    with pytest.raises(ValueError, match="nl"):
        Translator({"en": {}})


def test_has_key() -> None:
    translator = Translator(CATALOGS)

    assert translator.has_key("greeting")
    assert not translator.has_key("nope")
