"""Locale negotiation: decide which enabled locale a visitor gets.

Pure functions without I/O. Locale codes are normalised to lower case with a hyphen
(``en-gb``); anything that does not look like a language tag is ignored, so header and
cookie values can never inject arbitrary strings into templates or catalogue lookups.
"""

import re
from collections.abc import Iterable, Sequence

DEFAULT_LOCALE = "nl"
"""Dutch is the default for every visitor whose preferences match nothing enabled."""

MAX_ACCEPT_LANGUAGE_ENTRIES = 32
"""Entries beyond this are ignored; browsers send a handful."""

_MAX_HEADER_CHARS = 4096
_LOCALE_PATTERN = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")


def normalise_locale(raw: str | None) -> str | None:
    """Return ``raw`` as a normalised locale code, or ``None`` if it is not one.

    ``"EN_gb"`` becomes ``"en-gb"``; ``"../etc"`` and ``"x"`` become ``None``.
    """
    if raw is None:
        return None
    candidate = raw.strip().lower().replace("_", "-")
    return candidate if _LOCALE_PATTERN.fullmatch(candidate) else None


def _quality(params: Iterable[str]) -> float | None:
    """Return the ``q`` weight from Accept-Language parameters, ``None`` if invalid."""
    for param in params:
        name, _, value = param.strip().partition("=")
        if name.strip().lower() == "q":
            try:
                q = float(value)
            except ValueError:
                return None
            return q if 0 < q <= 1 else None
    return 1.0


def parse_accept_language(header: str) -> list[str]:
    """Return the locales of an ``Accept-Language`` header, most preferred first.

    Entries with ``q=0``, an invalid weight, the ``*`` wildcard or a malformed tag are
    dropped. Equal weights keep header order. At most ``MAX_ACCEPT_LANGUAGE_ENTRIES`` are
    returned.
    """
    weighted: list[tuple[float, str]] = []
    for entry in header[:_MAX_HEADER_CHARS].split(","):
        tag, *params = entry.split(";")
        locale = normalise_locale(tag)
        q = _quality(params)
        if locale is None or q is None:
            continue
        weighted.append((q, locale))
        if len(weighted) == MAX_ACCEPT_LANGUAGE_ENTRIES:
            break
    weighted.sort(key=lambda item: -item[0])  # stable: equal weights keep header order
    return [locale for _, locale in weighted]


def match_locale(candidate: str | None, enabled: Sequence[str]) -> str | None:
    """Return the enabled locale for ``candidate``, falling back from region to language.

    ``"en-GB"`` matches an enabled ``"en"``. Returns ``None`` when nothing matches.
    """
    locale = normalise_locale(candidate)
    if locale is None:
        return None
    if locale in enabled:
        return locale
    language = locale.split("-", 1)[0]
    return language if language in enabled else None


def negotiate_locale(
    *,
    enabled: Sequence[str],
    explicit: str | None = None,
    cookie: str | None = None,
    accept_language: str | None = None,
    organisation_default: str | None = None,
) -> str:
    """Pick the locale for a request; the first source with an enabled match wins.

    Order: ``explicit`` (``?lang=``), ``cookie``, ``accept_language`` (by weight),
    ``organisation_default``, then ``DEFAULT_LOCALE`` (or the first enabled locale if Dutch
    is disabled).

    Args:
        enabled: Normalised codes of the locales that may be served.
        explicit: Choice made in the language switcher.
        cookie: Value of the ``lang`` cookie.
        accept_language: Raw ``Accept-Language`` header.
        organisation_default: The organisation's configured default locale.

    Raises:
        ValueError: If no locale is enabled.
    """
    if not enabled:
        raise ValueError("at least one enabled locale is required")
    for candidate in (explicit, cookie):
        if match := match_locale(candidate, enabled):
            return match
    for candidate in parse_accept_language(accept_language or ""):
        if match := match_locale(candidate, enabled):
            return match
    if match := match_locale(organisation_default, enabled):
        return match
    return DEFAULT_LOCALE if DEFAULT_LOCALE in enabled else enabled[0]
