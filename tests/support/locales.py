"""Locales for tests: the required ones and a request locale for each."""

from app.i18n import CATALOGUE_DIR, Translator, load_catalogues
from app.web.locale import RequestLocale

REQUIRED_LOCALES = ("nl", "en")
"""The locales every catalogue ships (CLAUDE.md: new keys need both)."""


def locale_of(code: str) -> RequestLocale:
    """Return the locale ``code`` as a request has it."""
    translator = Translator(load_catalogues(CATALOGUE_DIR))
    return RequestLocale(translator.bind(code), remember=False, translator=translator)


def nl_locale() -> RequestLocale:
    """Return the Dutch locale."""
    return locale_of("nl")
