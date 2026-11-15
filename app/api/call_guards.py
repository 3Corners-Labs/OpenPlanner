"""What every route of the call page asks first, in one place (ADR 0123 steps 5c, 6a and 7c).

The call page's routes (``app.api.call_pages``, ``app.api.call_chat_pages``) refuse in one order, so
that an outsider learns nothing about a service: the platform switch (``require_switch``, before
anything is looked up), the participant's session for the booking (``app.api.link_session``) and
only then the booking's service (``require_service``). ``staff_name_json`` is the one place that
turns the record of who accepted a booking into what the participant is told.
"""

from fastapi import Request
from starlette.exceptions import HTTPException

from app.domain.call_client import uses_call_page
from app.domain.guest_access import StaffNameMode
from app.domain.services import BookingDetails
from app.domain.staff_screen_names import StaffScreenNames
from app.web.organisation_settings import follow


def require_switch(request: Request) -> None:
    """Refuse as an address that does not exist while the platform switch is off.

    Raises:
        HTTPException: ``404``, before anything about the booking is looked at.
    """
    if not request.app.state.settings.embedded_call_enabled:
        raise HTTPException(status_code=404)


def require_service(request: Request, details: BookingDetails) -> None:
    """Refuse as an address that does not exist for a booking of a service that did not choose us.

    Run after the participant's session was checked, so only they can meet it (ADR 0123 step 6a).

    Raises:
        HTTPException: ``404`` unless the booking's service uses the call page now.
    """
    enabled = request.app.state.settings.embedded_call_enabled
    if not uses_call_page(details.group, platform_enabled=enabled):
        raise HTTPException(status_code=404)


async def staff_name_json(request: Request, details: BookingDetails) -> dict[str, str]:
    """Return ``{"name": ...}`` or ``{}``: who the participant talks to, by the organisation.

    From our own record of who accepted the booking, never from the video platform and never an
    identifier of the staff member (``app.domain.screen_name``).
    """
    current = follow(request, details.group.organisation_id)
    names: StaffScreenNames = request.app.state.staff_names
    claim = details.booking.claim
    label = await names.label_of(
        claim.claimed_by if claim else None, StaffNameMode(current.embedded_call_staff_name)
    )
    return label.as_json()
