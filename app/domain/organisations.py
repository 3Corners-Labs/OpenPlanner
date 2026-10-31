"""Organisation use cases: list and open client organisations, create and edit them.

``organisation.view`` (ADR 0047 step 3) opens an organisation's page within the admin's
reach: the superuser sees every organisation, an organisation admin and the auditor their
own. ``organisation.manage`` is a global permission held only by the superuser (HANDOVER §6,
ADR 0014). Every operation asks ``AdminService.authorize`` first; every change runs in one
locked transaction and is audited with what changed (field names, never values).

    organisations  organisation.view     the organisations in the admin's scope
    view           organisation.view     one organisation in the admin's scope
    get            organisation.manage   one organisation, for the edit form
    create         organisation.manage   name, slug (fixed from then on), default locale
    update         organisation.manage   name and default locale; slug and branding stay

Deleting an organisation is not offered: it would remove groups and bookings whose personal
data has its own retention rules (CLAUDE.md, privacy rules).
"""

from collections.abc import Sequence
from dataclasses import dataclass, fields, replace

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import RESERVED_SLUGS, Organisation

_PERMISSION = Permission.ORGANISATION_MANAGE


@dataclass(frozen=True)
class NewOrganisation:
    """What the superuser enters to create an organisation; branding starts at the defaults."""

    name: str
    slug: str
    default_locale: str


@dataclass(frozen=True)
class OrganisationSettings:
    """The editable settings of an organisation (its slug never changes)."""

    name: str
    default_locale: str


class OrganisationService:
    """Application service for managing organisations.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises every operation and audits refusals.
        locales: The locales with a catalogue; an organisation's default must be one of them.

    Raises:
        ValueError: If ``locales`` is empty.
    """

    def __init__(
        self, uow: UnitOfWork, clock: Clock, admin: AdminService, locales: Sequence[str]
    ) -> None:
        """Store the collaborators and the allowed locales."""
        if not locales:
            raise ValueError("at least one locale is needed")
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._locales = frozenset(locales)

    async def organisations(self, principal: Principal) -> list[Organisation]:
        """Return the organisations ``principal`` may open, by slug (database-side scope).

        Raises:
            DomainError: ``not_permitted`` when the admin may open none.
        """
        require(await self._admin.authorize_anywhere(principal, Permission.ORGANISATION_VIEW))
        async with self._uow.read() as tx:
            return await tx.list_organisations(scope_for(principal, Permission.ORGANISATION_VIEW))

    async def view(self, principal: Principal, slug: str) -> Organisation:
        """Return the organisation with ``slug`` if ``principal`` may open its page.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        require(
            await self._admin.authorize(
                principal, Permission.ORGANISATION_VIEW, organisation_slug=slug
            )
        )
        async with self._uow.read() as tx:
            return await organisation_by_slug(tx, slug)

    async def get(self, principal: Principal, slug: str) -> Organisation:
        """Return the organisation with ``slug`` for the superuser's edit form.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize(principal)
        async with self._uow.read() as tx:
            return await organisation_by_slug(tx, slug)

    async def create(self, principal: Principal, new: NewOrganisation) -> Organisation:
        """Create an organisation with default branding.

        Raises:
            DomainError: ``not_permitted``, ``slug_taken`` or ``invalid_configuration`` with
                the field.
        """
        await self._authorize(principal)
        organisation = Organisation(0, new.name, new.slug, self._locale(new.default_locale))
        async with self._uow.locked() as tx:
            if new.slug in RESERVED_SLUGS or await tx.get_organisation(new.slug) is not None:
                raise DomainError(ErrorCode.SLUG_TAKEN, "slug")
            stored = await tx.add_organisation(organisation)
            await self._audit(tx, principal, stored.slug, "create")
        return stored

    async def update(
        self, principal: Principal, slug: str, settings: OrganisationSettings
    ) -> list[str]:
        """Apply ``settings``; return the names of the changed fields.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``invalid_configuration`` with the field.
        """
        await self._authorize(principal)
        self._locale(settings.default_locale)
        async with self._uow.locked() as tx:
            current = await organisation_by_slug(tx, slug)
            updated = replace(
                current, **{f.name: getattr(settings, f.name) for f in fields(settings)}
            )
            changed = sorted(
                f.name
                for f in fields(settings)
                if getattr(current, f.name) != getattr(updated, f.name)
            )
            if changed:
                await tx.save_organisation(updated)
                await self._audit(tx, principal, slug, ",".join(changed))
        return changed

    def _locale(self, locale: str) -> str:
        """Return ``locale`` if it has a catalogue.

        Raises:
            DomainError: ``invalid_configuration`` for ``default_locale``.
        """
        if locale not in self._locales:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "default_locale")
        return locale

    async def _authorize(self, principal: Principal) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``organisation.manage``."""
        require(await self._admin.authorize(principal, _PERMISSION))

    async def _audit(self, tx: Transaction, principal: Principal, slug: str, detail: str) -> None:
        """Record a change of organisation ``slug``."""
        await record_change(tx, self._clock.now(), principal, _PERMISSION, slug, None, detail)


async def organisation_by_slug(tx: Transaction, slug: str) -> Organisation:
    """Return the organisation with ``slug`` (shared by every organisation-level use case).

    Raises:
        DomainError: ``organisation_not_found``.
    """
    organisation = await tx.get_organisation(slug)
    if organisation is None:
        raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
    return organisation
