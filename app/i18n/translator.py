"""Message lookup: from a dotted key to the text a visitor sees.

Lookup chain, first hit wins (ADR 0004):

1. group override (when a group is given; the term of ADR 0077)
2. organisation override (when an organisation is given; ADR 0074)
3. global override (not built)
4. shipped catalogue of the requested locale
5. shipped catalogue of the default locale (Dutch)
6. the key itself, logged once per key and locale as ``i18n.missing_key``

Some placeholders are not filled by the caller but derived from other texts of the catalogue,
such as the agent term (``{agent}``, ``{agents}``, ``{Agent}``, ``{Agents}``, ADR 0077): a
``DerivedPlaceholders`` names them and computes them from the texts in effect for the
organisation and group. A value the caller passes wins.
"""

from collections.abc import Callable, Mapping
from typing import Protocol

import structlog

from app.i18n.formatting import format_message, placeholders
from app.i18n.negotiation import DEFAULT_LOCALE
from app.i18n.terms import AgentTermPlaceholders

log = structlog.get_logger()


class DerivedPlaceholders(Protocol):
    """Placeholders whose values come from other catalogue texts, not from the caller."""

    names: frozenset[str]

    def values(self, lookup: Callable[[str], str]) -> Mapping[str, str]:
        """Return the value of every name; ``lookup(key)`` gives the text of a catalogue key."""
        ...


class OverrideSource(Protocol):
    """Texts edited in the admin UI, stored outside the shipped catalogues."""

    def get(
        self, locale: str, key: str, organisation_id: int | None, group_id: int | None = None
    ) -> str | None:
        """Return the override for ``key`` in ``locale``, or ``None``.

        ``group_id`` asks for a group's own text (only the agent term, ADR 0077);
        ``organisation_id=None`` asks for the global (superuser) override.
        """
        ...

    async def refresh(self) -> None:
        """Make ``get`` reflect recent changes; cheap when called often (it may cache)."""
        ...


class NoOverrides:
    """``OverrideSource`` without any overrides; used by tools that need no database."""

    def get(
        self, locale: str, key: str, organisation_id: int | None, group_id: int | None = None
    ) -> str | None:
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
        derived: Placeholders computed from other texts; defaults to the agent term.

    Raises:
        ValueError: If the default locale has no catalogue.
    """

    def __init__(
        self,
        catalogues: Mapping[str, Mapping[str, str]],
        overrides: OverrideSource | None = None,
        default_locale: str = DEFAULT_LOCALE,
        derived: DerivedPlaceholders | None = None,
    ) -> None:
        """Copy the catalogues and check that the default locale is among them."""
        if default_locale not in catalogues:
            raise ValueError(f"no catalogue for the default locale {default_locale!r}")
        self._catalogues = {locale: dict(messages) for locale, messages in catalogues.items()}
        self._overrides: OverrideSource = overrides or NoOverrides()
        self._default = default_locale
        self._derived: DerivedPlaceholders = derived or AgentTermPlaceholders()
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

    def lookup(
        self,
        locale: str,
        key: str,
        organisation_id: int | None = None,
        group_id: int | None = None,
    ) -> str:
        """Return the unformatted text for ``key`` following the lookup chain."""
        if group_id is not None:
            text = self._overrides.get(locale, key, organisation_id, group_id)
            if text is not None:
                return text
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
        self,
        locale: str,
        key: str,
        *,
        organisation_id: int | None = None,
        group_id: int | None = None,
        **params: object,
    ) -> str:
        """Return the text for ``key`` in ``locale`` with placeholders filled from ``params``.

        Derived placeholders (the agent term) are filled from the texts in effect for the
        organisation and group, unless ``params`` has a value for them.
        """
        text = self.lookup(locale, key, organisation_id, group_id)
        return self.format(text, locale, organisation_id, group_id, **params)

    def format(
        self,
        text: str,
        locale: str,
        organisation_id: int | None = None,
        group_id: int | None = None,
        **params: object,
    ) -> str:
        """Fill the placeholders of ``text`` that is not from the catalogue, such as a group's own.

        The derived placeholders come from the texts in effect for the organisation and group.
        """
        if placeholders(text) & self._derived.names:
            values = self._derived.values(
                lambda k: self.lookup(locale, k, organisation_id, group_id)
            )
            params = {**values, **params}
        return format_message(text, params)

    def bind(
        self, locale: str, organisation_id: int | None = None, group_id: int | None = None
    ) -> "BoundGettext":
        """Return a ``_()`` callable fixed to one locale, organisation and group."""
        return BoundGettext(self, locale, organisation_id, group_id)


class BoundGettext:
    """``_(key, **params)`` for one locale; what templates and handlers call."""

    def __init__(
        self,
        translator: Translator,
        locale: str,
        organisation_id: int | None = None,
        group_id: int | None = None,
    ) -> None:
        """Bind ``translator`` to ``locale`` and optionally an organisation and a group."""
        self._translator = translator
        self.locale = locale
        self.organisation_id = organisation_id
        self.group_id = group_id

    def scope_to(self, organisation_id: int | None, group_id: int | None) -> None:
        """Move this binding to an organisation and group, once the request knows them."""
        self.organisation_id = organisation_id
        self.group_id = group_id

    def format(self, text: str, /, **params: object) -> str:
        """Fill ``text``, which is not from the catalogue, as a text translated by this binding."""
        return self._translator.format(
            text, self.locale, self.organisation_id, self.group_id, **params
        )

    def __call__(self, key: str, /, **params: object) -> str:
        """Translate ``key`` with ``params``."""
        return self._translator.gettext(
            self.locale,
            key,
            organisation_id=self.organisation_id,
            group_id=self.group_id,
            **params,
        )
