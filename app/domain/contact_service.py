"""Contact block use cases: alternative contact options of organisations and groups (ADR 0037 §2).

The organisation default is changed with ``organisation.closure`` on the organisation
(organisation admin and superuser): it belongs with preparing for an emergency closure. A group
may override it with its own block (another desk, another number), changed with
``closure.manage`` on the group. Every change asks ``AdminService.authorize`` first and is
audited in the same transaction with the number of options per kind, never numbers, addresses
or texts.

    public       citizen pages (closure, maintenance), no authorisation: the block is public
    view         organisation.closure, organisation level
    save         organisation.closure, organisation level; an empty block removes it
    view_group   closure.manage, group level; also shows the organisation default
    save_group   closure.manage, group level; an empty block removes it (default applies)

``effective_block`` is the one rule for which block applies to a group: its own, else the
organisation's. A closure copies it when it starts.
"""

from dataclasses import dataclass

from app.domain.admin import AdminService, Authorization, record_change, require
from app.domain.authorization import Permission, Principal
from app.domain.contacts import ContactBlock
from app.domain.lookups import group_by_slug
from app.domain.organisations import organisation_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, Organisation


@dataclass(frozen=True)
class OrganisationContacts:
    """An organisation with its default contact block, for the admin page."""

    organisation: Organisation
    block: ContactBlock | None


@dataclass(frozen=True)
class GroupContacts:
    """A group with its own contact block and the organisation default it falls back to."""

    group: Group
    block: ContactBlock | None
    organisation_default: ContactBlock | None


async def effective_block(tx: Transaction, group: Group) -> ContactBlock | None:
    """Return the contact block that applies to ``group``: its own, else the organisation's."""
    own = await tx.get_group_contact_block(group.id)
    return own if own is not None else await tx.get_contact_block(group.organisation_id)


def contacts_detail(action: str, block: ContactBlock | None) -> str:
    """Return the audit detail of a stored or removed block: counts per kind, never values."""
    return f"{action}.update,{block.counts}" if block else f"{action}.remove"


class ContactService:
    """Application service for the contact blocks of organisations and groups.

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

    async def public_for_group(self, group: Group) -> ContactBlock | None:
        """Return the contact block that applies to ``group`` for citizens (ADR 0038 §4)."""
        async with self._uow.read() as tx:
            return await effective_block(tx, group)

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
            await record_change(
                tx,
                self._clock.now(),
                principal,
                Permission.ORGANISATION_CLOSURE,
                slug,
                None,
                contacts_detail("contacts", stored),
            )
        return True

    async def view_group(self, principal: Principal, slug: str) -> GroupContacts:
        """Return the group with ``slug``, its own block and the organisation default.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        await self._authorize_group(principal, slug)
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            return GroupContacts(
                group,
                await tx.get_group_contact_block(group.id),
                await tx.get_contact_block(group.organisation_id),
            )

    async def save_group(self, principal: Principal, slug: str, block: ContactBlock) -> bool:
        """Store the group's own block, or remove it when empty; return whether it changed.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        authorization = await self._authorize_group(principal, slug)
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            current = await tx.get_group_contact_block(group.id)
            stored = None if block.is_empty else block
            if stored == current:
                return False
            await tx.save_group_contact_block(group.id, stored)
            await record_change(
                tx,
                self._clock.now(),
                principal,
                Permission.CLOSURE_MANAGE,
                authorization.target.organisation,
                slug,
                contacts_detail("contacts", stored),
            )
        return True

    async def _authorize(self, principal: Principal, slug: str) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``organisation.closure`` on ``slug``."""
        require(
            await self._admin.authorize(
                principal, Permission.ORGANISATION_CLOSURE, organisation_slug=slug
            )
        )

    async def _authorize_group(self, principal: Principal, slug: str) -> Authorization:
        """Raise ``not_permitted`` unless the matrix grants ``closure.manage`` on group ``slug``."""
        return require(
            await self._admin.authorize(principal, Permission.CLOSURE_MANAGE, group_slug=slug)
        )
