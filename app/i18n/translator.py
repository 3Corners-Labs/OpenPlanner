"""Message lookup: from a dotted key to the text a visitor sees.

Lookup chain, first hit wins (ADR 0004):

1. organisation override (when an organisation is given; ADR 0074)
2. global override (not built)
3. shipped catalogue of the requested locale
4. shipped catalogue of the default locale (Dutch)
5. the key itself, logged once per key and locale as ``i18n.missing_key``
"""

from collections.abc import Mapping
from typing import Protocol

import structlog

from app.i18n.formatting import format_message
from app.i18n.negotiation import DEFAULT_LOCALE

log = structlog.get_logger()


class OverrideSource(Protocol):
    """Texts edited in the admin UI, stored outside the shipped catalogues."""

    def get(self, locale: str, key: str, organisation_id: int | None) -> str | None:
        """Return the override for ``key`` in ``locale``, or ``None``.

        ``organisation_id=None`` asks for the global (superuser) override.
        """
        ...

    async def refresh(self) -> None:
        """Make ``get`` reflect recent changes; cheap when called often (it may cache)."""
        ...


class NoOverrides:
    """``OverrideSource`` without any overrides; used by tools that need no database."""

    def get(self, locale: str, key: str, organisation_id: int | None) -> str | None:
        """Return ``None``: there are no overrides."""
        return None

    async def refresh(self) -> None:
        """Do nothing: there is nothing to refresh."""


class Translator:
    """Resolve and format messages for any enabled locale.

    Args:
        catalogues: Shipped messages per locale (``{"nl": {"home.title": "..."}}``).
        overrides: Admin-edited texts; defaults to none.
        default_locale: Final catalogue fallback; must be present in ``catalogues``.

    Raises:
        ValueError: If the default locale has no catalogue.
    """

    def __init__(
        self,
        catalogues: Mapping[str, Mapping[str, str]],
        overrides: OverrideSource | None = None,
        default_locale: str = DEFAULT_LOCALE,
    ) -> None:
        """Copy the catalogues and check that the default locale is among them."""
        if default_locale not in catalogues:
            raise ValueError(f"no catalogue for the default locale {default_locale!r}")
        self._catalogues = {locale: dict(messages) for locale, messages in catalogues.items()}
        self._overrides: OverrideSource = overrides or NoOverrides()
        self._default = default_locale
        self._reported_missing: set[tuple[str, str]] = set()

    async def refresh(self) -> None:
        """Bring the override source up to date; call once per request or job."""
        await self._overrides.refresh()

    @property
    def locales(self) -> tuple[str, ...]:
        """Locales with a shipped catalogue, the default locale first."""
        others = sorted(locale for locale in self._catalogues if locale != self._default)
        return (self._default, *others)

    def has_key(self, key: str) -> bool:
        """Return whether ``key`` exists in the default catalogue."""
        return key in self._catalogues[self._default]

    def lookup(self, locale: str, key: str, organisation_id: int | None = None) -> str:
        """Return the unformatted text for ``key`` following the lookup chain."""
        if organisation_id is not None:
            text = self._overrides.get(locale, key, organisation_id)
            if text is not None:
                return text
        text = self._overrides.get(locale, key, None)
        if text is not None:
            return text
        for catalogue_locale in (locale, self._default):
            text = self._catalogues.get(catalogue_locale, {}).get(key)
            if text is not None:
                return text
        if (locale, key) not in self._reported_missing:
            self._reported_missing.add((locale, key))
            log.warning("i18n.missing_key", key=key, locale=locale)
        return key

    def gettext(
        self, locale: str, key: str, *, organisation_id: int | None = None, **params: object
    ) -> str:
        """Return the text for ``key`` in ``locale`` with placeholders filled from ``params``."""
        return format_message(self.lookup(locale, key, organisation_id), params)

    def bind(self, locale: str, organisation_id: int | None = None) -> "BoundGettext":
        """Return a ``_()`` callable fixed to one locale and organisation."""
        return BoundGettext(self, locale, organisation_id)


class BoundGettext:
    """``_(key, **params)`` for one locale; what templates and handlers call."""

    def __init__(
        self, translator: Translator, locale: str, organisation_id: int | None = None
    ) -> None:
        """Bind ``translator`` to ``locale`` and optionally an organisation."""
        self._translator = translator
        self.locale = locale
        self.organisation_id = organisation_id

    def __call__(self, key: str, /, **params: object) -> str:
        """Translate ``key`` with ``params``."""
        return self._translator.gettext(
            self.locale, key, organisation_id=self.organisation_id, **params
        )
