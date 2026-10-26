"""Look-ups that refuse with a domain error when nothing is found, shared by the use cases."""

from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Transaction
from app.domain.scheduling import Group, Organisation


async def group_by_slug(tx: Transaction, slug: str) -> Group:
    """Return the group with ``slug``.

    Raises:
        DomainError: ``group_not_found``.
    """
    group = await tx.get_group(slug)
    if group is None:
        raise DomainError(ErrorCode.GROUP_NOT_FOUND)
    return group


async def organisation_by_slug(tx: Transaction, slug: str) -> Organisation:
    """Return the organisation with ``slug``.

    Raises:
        DomainError: ``organisation_not_found``.
    """
    organisation = await tx.get_organisation(slug)
    if organisation is None:
        raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
    return organisation
