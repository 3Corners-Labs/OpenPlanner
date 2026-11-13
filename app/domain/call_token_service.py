"""The guest token for the participant's call page (ADR 0123 points 6 and 7).

    issue ─► the booking behind the authorised reference
          ─► inside the join window of ``/join/go`` (``join_state``)? else ``NOT_OPEN``
          ─► the guest is remembered on the booking (``guest_minted_at``, no personal data)
          ─► the provider mints for the booking's subject under the chosen name
             (refused or unreachable: ``UNAVAILABLE``, one ``integration_error`` on the timeline)
          ─► the token, when it stops being accepted, and the meeting's guest link

The booking is marked *before* the provider is asked, so a guest that exists at the provider
always has the mark the purge needs to rename it (point 7); a failed minting leaves a mark for
a guest that was never made, and the purge then names a nameless one at worst. The token and the
links are returned to the caller and nowhere else: never stored, never put on the timeline,
never in an error. The rule "who may ask" is the caller's (the participant's session and the
form protection); the rules "when" (the join window) and "what is sent" (subject and name) are
here.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.booking import Booking
from app.domain.calls import EventKind, JoinState, join_link, join_state
from app.domain.errors import DomainError, ErrorCode
from app.domain.guest_access import DisplayNameMode, GuestAccess, guest_display_name, guest_subject
from app.domain.integration import IntegrationService
from app.domain.participant_links import PublicRef
from app.domain.ports import Clock, IntegrationError, UnitOfWork, VideoProvider
from app.domain.scheduling import JoinClient


class TokenRefusal(StrEnum):
    """Why no token was handed out, for a booking that exists."""

    NOT_OPEN = "not_open"
    """Not confirmed, not inside the join window, or without a meeting: what ``/join/go`` sends
    back to the status page for."""
    UNAVAILABLE = "unavailable"
    """The video platform is not configured, refused or could not be reached."""


@dataclass(frozen=True)
class CallToken:
    """What the call page gets: the guest token and the meeting's guest link.

    Attributes:
        access: The token and when the provider stops accepting it; the token stays out of
            ``repr``.
        link: The meeting's guest link (https only), never the host's.
    """

    access: GuestAccess
    link: str = ""


class CallTokenService:
    """Mints guest tokens for participants who join in the call page.

    Args:
        uow: Reads the booking and remembers that a guest was minted for it.
        video: The video provider, or ``None`` when none is configured.
        integration: Records a failed minting on the booking's timeline.
        clock: The current time.
        key: The key of the guest subjects (``app.keys.guest_pseudonym_key``).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        video: VideoProvider | None,
        integration: IntegrationService,
        clock: Clock,
        key: bytes,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._video = video
        self._integration = integration
        self._clock = clock
        self._key = key

    async def issue(
        self, ref: PublicRef, display: DisplayNameMode, term: str
    ) -> CallToken | TokenRefusal:
        """Mint a guest token for the booking behind ``ref``, or say why not.

        Args:
            ref: The booking, after the caller checked the participant's session.
            display: What the guest is called (``embedded_call_display_name`` of the booking's
                organisation).
            term: The organisation's word for a participant, for ``TERM`` and as the fallback.

        Returns:
            The token and the guest link, or ``NOT_OPEN`` or ``UNAVAILABLE``.

        Raises:
            DomainError: ``booking_not_found``.
        """
        async with self._uow.read() as tx:
            booking = await tx.get_booking_by_public_ref(ref.value)
        if booking is None or booking.id is None:
            raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
        now = self._clock.now()
        # The one rule of the join page (``join_state``) and its https-only link (``join_link``).
        link = join_link(booking.meeting.link if booking.meeting else None, JoinClient.CHOICE)
        if join_state(booking, now) is not JoinState.OPEN or link is None:
            return TokenRefusal.NOT_OPEN
        if self._video is None:
            return TokenRefusal.UNAVAILABLE
        async with self._uow.locked() as tx:
            await tx.mark_guest_minted(booking.id, now)
        try:
            access = await self._video.guest_access(
                guest_subject(booking.id, self._key), _name(booking, display, term)
            )
        except IntegrationError as error:
            # The code only: the provider's reason is not asked for on this request (it carries
            # a name), and a token never exists on this path.
            await self._integration.record(
                booking.id, EventKind.INTEGRATION_ERROR, detail=error.code
            )
            return TokenRefusal.UNAVAILABLE
        return CallToken(access, link)


def _name(booking: Booking, display: DisplayNameMode, term: str) -> str:
    """Return the name the guest joins under: the first name or the organisation's word."""
    return guest_display_name(
        booking.contact.first_name if booking.contact else None, display, term
    )
