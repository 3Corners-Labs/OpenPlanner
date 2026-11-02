"""Open, check and sweep participant link sessions (ADR 0093).

    admit(booking, cookie value) ── live session? extend + add booking ──► same value
                                 └─ none / lapsed ── new session ──────► new value
    allows(cookie value, ref)    ── live session holding the booking? ──► True (touch if due)
    clean()                      ── delete lapsed sessions (worker housekeeping)

A lapsed or unknown value never fails loudly: the participant opens the link from the email
again, which always works. When the database cannot be read the error propagates: no session
means no access (fail closed).
"""

from datetime import timedelta

from app.domain.participant_links import hash_value, new_value, overflow
from app.domain.ports import Clock, UnitOfWork


class ParticipantLinkService:
    """Participant link sessions behind the cookie of the booking pages."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        """Keep the unit of work and the clock."""
        self._uow = uow
        self._clock = clock

    async def admit(self, booking_id: int, value: str | None, lifetime: timedelta) -> str:
        """Let the browser hold ``booking_id`` and return the cookie value to send.

        A live session of the browser is extended and gets the booking; otherwise a new
        session starts. Past 20 bookings the oldest drops out.

        Args:
            booking_id: A booking whose token the caller has just checked, or that the
                caller has just created for this browser.
            value: The cookie value the browser brought, if any.
            lifetime: Idle lifetime (``participant_link_session_hours``).
        """
        now = self._clock.now()
        async with self._uow.locked() as tx:
            session = await tx.get_link_session(hash_value(value)) if value else None
            if session is None or not session.alive(now):
                if session is not None:
                    await tx.delete_link_session(session.id)
                fresh = new_value()
                session_id = await tx.add_link_session(hash_value(fresh), now, now + lifetime)
                await tx.add_booking_to_link_session(session_id, booking_id, now)
                return fresh
            assert value is not None  # noqa: S101  # nosec B101 - a session was found by it
            await tx.touch_link_session(session.id, now, now + lifetime)
            if not session.holds(booking_id):
                await tx.add_booking_to_link_session(session.id, booking_id, now)
                for old in overflow((*session.booking_ids, booking_id)):
                    await tx.remove_booking_from_link_session(session.id, old)
            return value

    async def allows(self, value: str | None, public_ref: str, lifetime: timedelta) -> bool:
        """Return whether the cookie's live session holds the booking named by ``public_ref``.

        Whether the booking exists is not told apart from a session that lacks it: both are
        ``False``. A use extends the session, written at most once a minute.
        """
        if not value:
            return False
        now = self._clock.now()
        async with self._uow.read() as tx:
            session = await tx.get_link_session(hash_value(value))
            booking_id = await tx.get_booking_id_by_public_ref(public_ref)
        if session is None or not session.alive(now):
            return False
        if booking_id is None or not session.holds(booking_id):
            return False
        if session.due_for_touch(now):
            async with self._uow.locked() as tx:
                await tx.touch_link_session(session.id, now, now + lifetime)
        return True

    async def clean(self) -> int:
        """Delete the sessions that have lapsed; return how many went."""
        async with self._uow.locked() as tx:
            return await tx.delete_lapsed_link_sessions(self._clock.now())
