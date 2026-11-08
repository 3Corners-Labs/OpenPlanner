"""The participant's contact details for staff: on request, and every request audited.

The booking page of the agenda (``/admin/groups/{slug}/bookings/{id}``) is the one admin page
that may show a participant's personal data (ADR 0063 §4). It shows the name, email address
and phone number (ADR 0044 §2) only when a person with ``bookings.view`` on the group asks
for them (*Show contact details*), never by default: a page opened to trace a failed meeting,
or a screenshot of it for support, carries none. The product owner chose this on 2026-11-08,
so that a planner can call a participant whose booking is too late to change while each look
stays accountable. Every request is an audit event under ``bookings.view`` with the detail
``booking.contact_viewed``, the actor, the group and the booking id, never the data itself.
After the purge there is nothing to show, so nothing is audited. It is a protection, so there
is no setting to switch it off (ADR 0042).

    reveal  bookings.view  group level  the contact details of one booking of the group
"""

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal
from app.domain.booking import Contact
from app.domain.errors import DomainError, ErrorCode
from app.domain.lookups import group_by_slug
from app.domain.ports import Clock, UnitOfWork

CONTACT_VIEWED = "booking.contact_viewed"
"""The audit detail of a request for a participant's contact details."""


class ParticipantContactService:
    """Application service that shows a participant's contact details to staff, audited.

    Args:
        uow: Opens database transactions.
        clock: Current time, for the audit event.
        admin: Authorises every request and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    async def reveal(self, principal: Principal, slug: str, booking_id: int) -> Contact | None:
        """Return the contact details of one booking of the group, and audit the request.

        The audit event is written in the same transaction that reads the booking, so the
        details are only returned once the request is on record.

        Returns:
            The participant's contact, or ``None`` when their personal data have been deleted
            (nothing is audited then).

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``booking_not_found`` (also
                for a booking of another group, the same answer as for an unknown id).
        """
        target = require(
            await self._admin.authorize(principal, Permission.BOOKINGS_VIEW, group_slug=slug)
        ).target
        async with self._uow.locked() as tx:
            group = await group_by_slug(tx, slug)
            booking = await tx.get_booking(booking_id)
            if booking is None or booking.group_id != group.id:
                raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
            if booking.contact is None:
                return None
            await tx.add_audit_event(
                AuditEvent(
                    self._clock.now(),
                    principal.subject,
                    Permission.BOOKINGS_VIEW.value,
                    Outcome.SUCCESS,
                    "",
                    target.organisation,
                    target.group,
                    CONTACT_VIEWED,
                    booking_id=booking_id,
                )
            )
        return booking.contact
