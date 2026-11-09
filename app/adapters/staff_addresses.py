"""``StaffAddresses`` that reads the stored address first (ADR 0117 step 10b).

The address of a staff member is the verified ``email`` claim of their last login
(``staff.email``), whichever identity provider they log in through. Only a person without one
(logged in before step 10b, or the provider gave none) falls back to the bundled provider's
directory (ADR 0095). Nothing here logs an address.
"""

from app.domain.ports import StaffAddresses, UnitOfWork
from app.domain.staff import StaffStatus


class StoredAddresses:
    """Find a staff member's address in the staff record, else ask a fallback.

    Args:
        uow: Opens the read transaction for the staff record.
        fallback: The finder used when the record holds no address (the bundled directory).
    """

    def __init__(self, uow: UnitOfWork, fallback: StaffAddresses) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._fallback = fallback

    async def address_of(self, subject: str) -> str | None:
        """Return the stored address of ``subject``, else the fallback's, else ``None``.

        An erased person has none, and the fallback is not asked about them either.
        """
        async with self._uow.read() as tx:
            member = await tx.get_staff(subject)
        if member is not None and member.status is StaffStatus.ERASED:
            return None
        if member is not None and member.email:
            return member.email
        return await self._fallback.address_of(subject)
