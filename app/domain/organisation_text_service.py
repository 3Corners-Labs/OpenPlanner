"""Organisation texts: an organisation's own wording of participant pages and messages (ADR 0004).

An organisation admin (``texts.organisation.manage``, own organisations; the superuser for every
one) replaces the shipped text of an allow-listed key (``app.domain.text_overrides``) in one
locale. A group's own text still wins (ADR 0009); without either, the catalogue text applies.
Every change asks ``AdminService.authorize`` first, is validated whole-form-or-nothing and is
audited in the same transaction with the keys, never the texts.

    view    texts.organisation.manage   the organisation and its own texts by (locale, key)
    save    texts.organisation.manage   store or, with ``None``, remove texts of one locale
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from app.domain.admin import AdminService, record_change, require
from app.domain.agent_term import check_term_pair
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.organisations import organisation_by_slug
from app.domain.ports import Clock, UnitOfWork
from app.domain.role_names import ROLE_NAME_KEYS
from app.domain.scheduling import Organisation
from app.domain.text_overrides import EDITABLE_BY_KEY, OrganisationTexts, clean_override

_PERMISSION = Permission.TEXTS_ORGANISATION_MANAGE


@dataclass(frozen=True)
class OrganisationTextView:
    """An organisation with its own texts, for the editor."""

    organisation: Organisation
    texts: OrganisationTexts


class OrganisationTextService:
    """Application service for the texts of organisations.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises changes and audits refusals.
        locales: The locales a text can be written in (those with a shipped catalogue).
        role_names: The shipped name of every role per locale and role key, for the rule that
            a role name may not be the name of another role (ADR 0077).
        on_change: Called after texts were stored, so this process shows them at once.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        locales: Collection[str],
        role_names: Mapping[str, Mapping[str, str]] | None = None,
        *,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        """Store the collaborators."""
        self._role_names = role_names or {}
        self._on_change = on_change
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._locales = frozenset(locales)

    async def view(self, principal: Principal, slug: str) -> OrganisationTextView:
        """Return the organisation and its own texts by ``(locale, key)``.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            return OrganisationTextView(
                organisation, await tx.get_organisation_texts(organisation.id)
            )

    async def save(
        self, principal: Principal, slug: str, locale: str, texts: dict[str, str | None]
    ) -> list[str]:
        """Store the organisation's texts of one locale; ``None`` brings a key's default back.

        Every text is validated before anything is stored, so a form is saved whole or not at
        all. Returns the keys that changed, in the order given.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``;
                ``invalid_configuration`` with field ``locale``, ``key`` (a key off the
                allow-list) or the key of the first invalid text; a form that sets only one of the
                two words of the agent term names the other.
        """
        await self._authorize(principal, slug)
        if locale not in self._locales:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "locale")
        cleaned: dict[str, str | None] = {}
        for key, text in texts.items():
            if key not in EDITABLE_BY_KEY:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "key")
            taken = self._names_taken(locale, key, texts)
            cleaned[key] = clean_override(key, text, taken) if text is not None else None
        check_term_pair(cleaned)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            current = await tx.get_organisation_texts(organisation.id)
            changed = [k for k, v in cleaned.items() if current.get((locale, k)) != v]
            for key in changed:
                await tx.save_organisation_text(organisation.id, locale, key, cleaned[key])
            if changed:
                detail = f"texts.{locale}:" + ",".join(changed)
                await record_change(
                    tx, self._clock.now(), principal, _PERMISSION, slug, None, detail[:255]
                )
        if changed and self._on_change is not None:
            self._on_change()
        return changed

    def _names_taken(self, locale: str, key: str, texts: Mapping[str, str | None]) -> list[str]:
        """Return the names the role ``key`` may not get.

        Those are the shipped names of the other roles and the other role name in the same form.
        """
        if key not in ROLE_NAME_KEYS:
            return []
        shipped = self._role_names.get(locale, {})
        others = [name for role, name in shipped.items() if role != key]
        return others + [t for k, t in texts.items() if k in ROLE_NAME_KEYS and k != key and t]

    async def _authorize(self, principal: Principal, slug: str) -> None:
        """Raise ``not_permitted`` unless the matrix grants the permission on ``slug``."""
        require(await self._admin.authorize(principal, _PERMISSION, organisation_slug=slug))
