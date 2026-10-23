"""Internationalisation: one self-contained module for every user-facing text (ADR 0004).

The package imports nothing from the rest of ``app``; entry points call it. Public interface:

- ``negotiate_locale`` / ``parse_accept_language`` / ``normalise_locale``: which language a
  visitor gets (explicit choice → cookie → browser → organisation default → Dutch).
- ``Translator``: resolves a dotted key through the lookup chain (organisation override →
  global override → catalogue of the locale → Dutch catalogue → the key itself) and formats
  ``{name}`` placeholders safely.
- ``OverrideSource``: Protocol for admin-edited texts; the database adapter implements it.
- ``load_catalogues`` / ``CATALOGUE_DIR``: the shipped gettext ``.po`` catalogues.
- ``validate_override``: the rules an admin-edited text must satisfy.
"""

from app.i18n.formatting import InvalidOverride, format_message, placeholders, validate_override
from app.i18n.negotiation import (
    DEFAULT_LOCALE,
    match_locale,
    negotiate_locale,
    normalise_locale,
    parse_accept_language,
)
from app.i18n.po import CATALOGUE_DIR, load_catalogues
from app.i18n.translator import BoundGettext, NoOverrides, OverrideSource, Translator

__all__ = [
    "CATALOGUE_DIR",
    "DEFAULT_LOCALE",
    "BoundGettext",
    "InvalidOverride",
    "NoOverrides",
    "OverrideSource",
    "Translator",
    "format_message",
    "load_catalogues",
    "match_locale",
    "negotiate_locale",
    "normalise_locale",
    "parse_accept_language",
    "placeholders",
    "validate_override",
]
