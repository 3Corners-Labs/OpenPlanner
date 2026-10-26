"""Branding use cases: show an organisation's branding to citizens, let admins change it.

Changing branding needs ``branding.manage`` on the organisation (superuser only, HANDOVER §6).
Every change asks ``AdminService.authorize`` first and is audited in the same transaction,
with the names of what changed, never the values.

    public / for_group / logo        citizens, no authorisation (branding is public)
    update / set_logo / remove_logo  branding.manage, organisation level
"""

from dataclasses import replace

from app.domain.admin import AdminService, organisation_by_slug, record_change, require
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.branding import Branding, Logo
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, Organisation

_EDITABLE = ("display_name", "accent_color")


class BrandingService:
    """Application service for organisation branding.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises changes and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    async def public(self, slug: str) -> Organisation:
        """Return the organisation with ``slug`` and its branding.

        Raises:
            DomainError: ``organisation_not_found``.
        """
        async with self._uow.read() as tx:
            return await organisation_by_slug(tx, slug)

    async def for_group(self, group: Group) -> Organisation:
        """Return the organisation a group belongs to, with its branding.

        Raises:
            DomainError: ``organisation_not_found`` (only with an inconsistent database).
        """
        async with self._uow.read() as tx:
            organisation = await tx.get_organisation_by_id(group.organisation_id)
        if organisation is None:
            raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
        return organisation

    async def logo(self, slug: str) -> Logo | None:
        """Return the logo of the organisation with ``slug``, if it has one."""
        async with self._uow.read() as tx:
            return await tx.get_logo(slug)

    async def organisations(self, principal: Principal) -> list[Organisation]:
        """Return the organisations whose branding ``principal`` may change."""
        async with self._uow.read() as tx:
            return await tx.list_organisations(scope_for(principal, Permission.BRANDING_MANAGE))

    async def update(self, principal: Principal, slug: str, branding: Branding) -> list[str]:
        """Change display name and accent colour; return the names of the changed fields.

        The logo is kept; it changes only through ``set_logo`` and ``remove_logo``.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            current = organisation.branding
            changed = [n for n in sorted(_EDITABLE) if getattr(current, n) != getattr(branding, n)]
            if changed:
                await tx.save_branding(organisation.id, replace(branding, logo=current.logo))
                await self._audit(tx, principal, slug, ",".join(changed))
        return changed

    async def set_logo(self, principal: Principal, slug: str, logo: Logo) -> None:
        """Store a new logo, replacing the old one.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._save_logo(principal, slug, logo, "logo")

    async def remove_logo(self, principal: Principal, slug: str) -> None:
        """Remove the logo; pages then show the name only.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._save_logo(principal, slug, None, "logo.remove")

    async def _save_logo(
        self, principal: Principal, slug: str, logo: Logo | None, detail: str
    ) -> None:
        """Authorise, store or remove the logo, and audit it."""
        await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            await tx.save_logo(organisation.id, logo)
            await self._audit(tx, principal, slug, detail)

    async def _authorize(self, principal: Principal, slug: str) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``branding.manage`` on ``slug``."""
        require(
            await self._admin.authorize(
                principal, Permission.BRANDING_MANAGE, organisation_slug=slug
            )
        )

    async def _audit(self, tx: Transaction, principal: Principal, slug: str, detail: str) -> None:
        """Record a branding change of organisation ``slug``."""
        await record_change(
            tx, self._clock.now(), principal, Permission.BRANDING_MANAGE, slug, None, detail
        )
