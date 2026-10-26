"""Group look-up that refuses with ``group_not_found``, shared by the group-level use cases.

The organisation counterpart is ``app.domain.organisations.organisation_by_slug``.
"""

from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Transaction
from app.domain.scheduling import Group


async def group_by_slug(tx: Transaction, slug: str) -> Group:
    """Return the group with ``slug``.

    Raises:
        DomainError: ``group_not_found``.
    """
    group = await tx.get_group(slug)
    if group is None:
        raise DomainError(ErrorCode.GROUP_NOT_FOUND)
    return group
