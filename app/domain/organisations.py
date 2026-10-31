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
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.entitlements import is_idp_key
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import RESERVED_SLUGS, Organisation

_PERMISSION = Permission.ORGANISATION_MANAGE
IDP_KEY_CHANGED = "organisation.idp_key_changed"
DELETED = "deleted"
"""Detail of the ``organisation.delete`` audit event (ADR 0084 point 4)."""


@dataclass(frozen=True)
class Occupancy:
    """What still keeps an organisation from being deleted (ADR 0084 point 4)."""

    groups: int
    api_clients: int

    @property
    def empty(self) -> bool:
        """Whether nothing is left that refers to the organisation."""
        return not (self.groups or self.api_clients)


"""ADR 0049 point 4: the superuser changed the value the IdP names the organisation by."""


@dataclass(frozen=True)
class NewOrganisation:
    """What the superuser enters to create an organisation; branding starts at the defaults."""

    name: str
    slug: str
    default_locale: str


@dataclass(frozen=True)
class OrganisationSettings:
    """The editable settings of an organisation (its slug never changes).

    ``idp_key`` is the value the identity provider names the organisation by (ADR 0049
    point 4); ``None`` leaves it as it is.
    """

    name: str
    default_locale: str
    idp_key: str | None = None


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
            values = {
                f.name: getattr(settings, f.name)
                for f in fields(settings)
                if getattr(settings, f.name) is not None
            }
            updated = replace(current, **values)
            changed = sorted(name for name in values if getattr(current, name) != values[name])
            if "idp_key" in changed:
                await self._check_idp_key(tx, updated)
            if changed:
                await tx.save_organisation(updated)
                await self._audit(tx, principal, slug, ",".join(changed))
                if "idp_key" in changed:
                    await self._audit(
                        tx,
                        principal,
                        slug,
                        f"{current.idp_key}->{updated.idp_key}",
                        IDP_KEY_CHANGED,
                    )
        return changed

    async def occupancy(self, principal: Principal, slug: str) -> Occupancy:
        """Return what still keeps the organisation, for the delete page (ADR 0084).

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        require(await self._admin.authorize(principal, Permission.ORGANISATION_DELETE))
        async with self._uow.read() as tx:
            current = await organisation_by_slug(tx, slug)
            return Occupancy(
                await tx.count_groups(current.id), await tx.count_api_clients(current.id)
            )

    async def delete(self, principal: Principal, slug: str, confirmation: str) -> None:
        """Erase an empty organisation (ADR 0084 point 4).

        ``confirmation`` is the organisation's name typed back. Branding, channels and their
        secrets, embedding, contacts, texts, settings, scenarios and identity providers go
        with the row by the schema's cascades; groups and API clients keep it.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``confirmation_mismatch``, ``organisation_has_groups`` or
                ``organisation_has_api_clients``.
        """
        permission = Permission.ORGANISATION_DELETE
        require(await self._admin.authorize(principal, permission))
        async with self._uow.locked() as tx:
            current = await organisation_by_slug(tx, slug)
            if confirmation.strip().casefold() != current.name.casefold():
                raise DomainError(ErrorCode.CONFIRMATION_MISMATCH, "confirmation")
            if await tx.count_groups(current.id):
                raise DomainError(ErrorCode.ORGANISATION_HAS_GROUPS)
            if await tx.count_api_clients(current.id):
                raise DomainError(ErrorCode.ORGANISATION_HAS_API_CLIENTS)
            await record_change(tx, self._clock.now(), principal, permission, slug, None, DELETED)
            await tx.delete_organisation(current.id)

    async def holders_of(self, principal: Principal, slug: str) -> int:
        """Return how many people last held a grant on the organisation (ADR 0049 point 4).

        Shown with the IdP value field: they lose access until the IdP sends the new value.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize(principal)
        async with self._uow.read() as tx:
            await organisation_by_slug(tx, slug)
            holders = await tx.list_grant_holders(Scope(organisations=frozenset({slug})))
        return sum(h.holders for h in holders)

    @staticmethod
    async def _check_idp_key(tx: Transaction, updated: Organisation) -> None:
        """Refuse an IdP value outside the grammar or already used by another organisation.

        Raises:
            DomainError: ``invalid_configuration`` or ``idp_key_taken`` for ``idp_key``.
        """
        if not is_idp_key(updated.idp_key):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "idp_key")
        directory = await tx.get_directory()
        other = directory.organisation_by_idp_key(updated.idp_key)
        if other is not None and other.slug != updated.slug:
            raise DomainError(ErrorCode.IDP_KEY_TAKEN, "idp_key")

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

    async def _audit(
        self,
        tx: Transaction,
        principal: Principal,
        slug: str,
        detail: str,
        action: str | None = None,
    ) -> None:
        """Record a change of organisation ``slug`` (``action`` names a specific event)."""
        if action is None:
            await record_change(tx, self._clock.now(), principal, _PERMISSION, slug, None, detail)
            return
        await tx.add_audit_event(
            AuditEvent(
                self._clock.now(),
                principal.subject,
                action,
                Outcome.SUCCESS,
                "",
                slug,
                None,
                detail,
            )
        )


async def organisation_by_slug(tx: Transaction, slug: str) -> Organisation:
    """Return the organisation with ``slug`` (shared by every organisation-level use case).

    Raises:
        DomainError: ``organisation_not_found``.
    """
    organisation = await tx.get_organisation(slug)
    if organisation is None:
        raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
    return organisation
