"""The participant's contact details for staff: on request, and every request audited.

The booking page of the agenda (``/admin/groups/{slug}/bookings/{id}``) is the one admin page
that may show a participant's personal data (ADR 0063 §4). It shows the name, email address
and phone number (ADR 0044 §2) only when a person with ``bookings.view`` on the group asks
for them (*Show contact details*), never by default: a page opened to trace a failed meeting,
or a screenshot of it for support, carries none. The product owner chose this on 2026-11-08,
so that a planner can call a participant whose booking is too late to change while each look
stays accountable. Every request is an audit event under ``bookings.view`` with the detail
``booking.contact_viewed``, the actor, the group and the booking id, never the data itself.
After the purge there is nothing to show, so nothing is audited. What counts as a request is
the web edge's call: only the button on the booking page itself
(``app.web.protection.from_this_page``), never a shared or bookmarked address or a link on
another page. It is a protection, so there is no setting to switch it off (ADR 0042).

When one person makes more than ``contact_reveals_alert_per_hour`` requests (class C) within an
hour, a system event warns once (abuse case: a stolen session or an insider walking many booking
ids; ``app.domain.activity_alert``). Every request counts, also one for the same booking again,
as in the audit log; a refused request does not. It never refuses a request: the product owner
chose an alert on 2026-11-08, so that a planner calling many participants on a bad day is not
stopped.

    reveal  bookings.view  group level  the contact details of one booking of the group
"""

from collections.abc import Callable

from app.domain.activity_alert import ActivityAlert
from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal
from app.domain.booking import Contact
from app.domain.lookups import group_booking
from app.domain.ports import Clock, UnitOfWork
from app.domain.tunable import value_of

CONTACT_VIEWED = "booking.contact_viewed"
"""The audit detail of a request for a participant's contact details."""

REVEALS_ALERT = ActivityAlert(
    "booking.contact_reveals_alert",
    "too_many_contact_reveals",
    "reveals",
    frozenset({Permission.BOOKINGS_VIEW.value}),
    CONTACT_VIEWED,
)
"""The warning above ``contact_reveals_alert_per_hour`` requests by one person in an hour."""


class ParticipantContactService:
    """Application service that shows a participant's contact details to staff, audited.

    Args:
        uow: Opens database transactions.
        clock: Current time, for the audit event.
        admin: Authorises every request and audits refusals.
        alert_per_hour: ``contact_reveals_alert_per_hour`` (class C).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        alert_per_hour: int | Callable[[], int],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._alert_per_hour = alert_per_hour

    async def reveal(self, principal: Principal, slug: str, booking_id: int) -> Contact | None:
        """Return the contact details of one booking of the group, and audit the request.

        The audit event is written in the same transaction that reads the booking, so the
        details are only returned once the request is on record; so is the warning when this
        request takes the person above the hourly threshold.

        Returns:
            The participant's contact, or ``None`` when their personal data have been deleted
            (nothing is audited then).

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` (from the authorisation) or
                ``booking_not_found`` (also for a booking of another group, the same answer as
                for an unknown id).
        """
        target = require(
            await self._admin.authorize(principal, Permission.BOOKINGS_VIEW, group_slug=slug)
        ).target
        async with self._uow.locked() as tx:
            booking, _ = await group_booking(tx, slug, booking_id)
            if booking.contact is None:
                return None
            now = self._clock.now()
            await record_change(
                tx,
                now,
                principal,
                Permission.BOOKINGS_VIEW,
                target.organisation,
                target.group,
                CONTACT_VIEWED,
                booking_id=booking_id,
            )
            threshold = value_of(self._alert_per_hour)
            await REVEALS_ALERT.check(
                tx, now, principal.subject, threshold, target.organisation, target.group
            )
        return booking.contact
