"""Group look-ups that refuse with a domain error, shared by the group-level use cases.

``group_by_slug`` and ``group_summary_by_slug`` (the group with its organisation's slug and name)
refuse an unknown group with ``group_not_found``; ``group_in_organisation`` also refuses a group
that is another organisation's, with the same answer. ``group_booking`` refuses a booking that is
not the group's with ``booking_not_found``, the same answer as for an unknown id, so an id tried
through another group's address reveals nothing. The organisation counterpart is
``app.domain.organisations.organisation_by_slug``.
"""

from app.domain.booking import Booking
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Transaction
from app.domain.scheduling import Group, GroupSummary


async def group_by_slug(tx: Transaction, slug: str) -> Group:
    """Return the group with ``slug``.

    Raises:
        DomainError: ``group_not_found``.
    """
    group = await tx.get_group(slug)
    if group is None:
        raise DomainError(ErrorCode.GROUP_NOT_FOUND)
    return group


async def group_summary_by_slug(tx: Transaction, slug: str) -> GroupSummary:
    """Return the group with ``slug`` together with its organisation's slug and name.

    Raises:
        DomainError: ``group_not_found``.
    """
    summary = await tx.get_group_summary(slug)
    if summary is None:
        raise DomainError(ErrorCode.GROUP_NOT_FOUND)
    return summary


async def group_in_organisation(tx: Transaction, organisation_slug: str, slug: str) -> GroupSummary:
    """Return the group with ``slug`` if it belongs to the organisation ``organisation_slug``.

    An unknown group, another organisation's group and an unknown organisation are answered
    alike, so a group tried under another organisation's address reveals nothing.

    Raises:
        DomainError: ``group_not_found``.
    """
    summary = await group_summary_by_slug(tx, slug)
    if summary.organisation_slug != organisation_slug:
        raise DomainError(ErrorCode.GROUP_NOT_FOUND)
    return summary


async def group_booking(tx: Transaction, slug: str, booking_id: int) -> tuple[Booking, Group]:
    """Return the booking and its group if the booking belongs to group ``slug``.

    Raises:
        DomainError: ``booking_not_found`` (also for another group's booking, or an unknown
            group).
    """
    booking = await tx.get_booking(booking_id)
    group = await tx.get_group(slug)
    if booking is None or group is None or booking.group_id != group.id:
        raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
    return booking, group
