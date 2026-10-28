"""Contact block use cases: an organisation's alternative contact options (ADR 0037 §2).

The organisation default is changed with ``organisation.closure`` on the organisation
(organisation admin and superuser): it belongs with preparing for an emergency closure. Every
change asks ``AdminService.authorize`` first and is audited in the same transaction with the
number of options per kind, never numbers, addresses or texts.

    public   citizen pages (closure, maintenance), no authorisation: the block is public
    view     organisation.closure, organisation level
    save     organisation.closure, organisation level; an empty block removes it
"""

from dataclasses import dataclass

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal
from app.domain.contacts import ContactBlock
from app.domain.organisations import organisation_by_slug
from app.domain.ports import Clock, UnitOfWork
from app.domain.scheduling import Organisation


@dataclass(frozen=True)
class OrganisationContacts:
    """An organisation with its default contact block, for the admin page."""

    organisation: Organisation
    block: ContactBlock | None


class ContactService:
    """Application service for an organisation's default contact block.

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

    async def public(self, organisation_id: int) -> ContactBlock | None:
        """Return the organisation's contact block for citizens, if it has one."""
        async with self._uow.read() as tx:
            return await tx.get_contact_block(organisation_id)

    async def view(self, principal: Principal, slug: str) -> OrganisationContacts:
        """Return the organisation with ``slug`` and its contact block.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            return OrganisationContacts(organisation, await tx.get_contact_block(organisation.id))

    async def save(self, principal: Principal, slug: str, block: ContactBlock) -> bool:
        """Store the block, or remove it when it has no options; return whether it changed.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            current = await tx.get_contact_block(organisation.id)
            stored = None if block.is_empty else block
            if stored == current:
                return False
            await tx.save_contact_block(organisation.id, stored)
            detail = f"contacts.update,{block.counts}" if stored else "contacts.remove"
            await record_change(
                tx,
                self._clock.now(),
                principal,
                Permission.ORGANISATION_CLOSURE,
                slug,
                None,
                detail,
            )
        return True

    async def _authorize(self, principal: Principal, slug: str) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``organisation.closure`` on ``slug``."""
        require(
            await self._admin.authorize(
                principal, Permission.ORGANISATION_CLOSURE, organisation_slug=slug
            )
        )
