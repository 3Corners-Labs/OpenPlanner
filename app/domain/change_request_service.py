"""Use cases of a bot's change request and the participant's confirmation (ADR 0023 step 2).

    bot (machine API, scope bookings:request_change on the group)
        request(caller, group, phone, code, start)
            ─► switched on? code and phone well-formed? time free? ─► one locked transaction:
               the group's bookings with that phone number, the one whose code matches and that
               the participant could still change ─► request row (with or without a booking),
               the message to the participant, an audit event ─► the same answer whatever
               matched
        status(caller, id) ─► pending, confirmed or expired; only for the client that asked

    participant (management link)
        open_request(ref)  ─► the open request of the booking, for the confirm page
        confirm(ref)       ─► ``BookingService.cancel_in`` or ``reschedule_in``: the
                              participant's own rules (ADR 0069)

The phone number and code are never stored, logged or audited; the request row names only the
booking id. The rules of the code and the request are in ``app.domain.change_requests``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.domain import booking_changes
from app.domain.api_clients import ApiCaller
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import ApiScope
from app.domain.booking import Booking
from app.domain.booking_changes import sms_reachable
from app.domain.change_requests import (
    ChangeRequest,
    appointment_code,
    code_matches,
    new_request_id,
    normalise_code,
    request_expiry,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import normalise_phone
from app.domain.job_plan import JobType, plan_for_change_request
from app.domain.participant_links import PublicRef
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, GroupSummary
from app.domain.services import BookingDetails, BookingReceipt, BookingService

MATCHED = "matched"
NO_MATCH = "no_match"
"""Audit reasons of a change request: for the superuser's log, never in the API's answer."""


@dataclass(frozen=True)
class ChangePolicy:
    """An organisation's settings for change requests (class D, ADR 0023 step 2).

    Attributes:
        enabled: ``agent_change_requests_enabled``.
        minutes: ``agent_change_request_minutes``: how long the participant can confirm.
    """

    enabled: bool = False
    minutes: int = 60


@dataclass(frozen=True)
class OpenChange:
    """A booking with the change request its participant can confirm now."""

    details: BookingDetails
    request: ChangeRequest


@dataclass(frozen=True)
class ConfirmedChange:
    """What confirming did: the cancelled booking, or the receipt of the moved one."""

    request: ChangeRequest
    cancelled: Booking | None = None
    moved: BookingReceipt | None = None


class ChangeRequestService:
    """Takes a bot's change request and applies it once the participant confirms.

    Args:
        uow: The unit of work.
        clock: The clock.
        bookings: The booking service: availability, lookup by link, the participant's moves.
        token_keys: ``MANAGEMENT_TOKEN_KEY``, then the previous key during a rotation (ADR 0094).
        policy: The organisation's ``ChangePolicy``, by organisation id (read live).
        phone_region: The organisation's default phone region (ADR 0055), by organisation id.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        bookings: BookingService,
        token_keys: tuple[bytes, ...],
        policy: Callable[[int], ChangePolicy],
        phone_region: Callable[[int], str],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._bookings = bookings
        self._keys = token_keys
        self._policy = policy
        self._phone_region = phone_region

    def code(self, booking: Booking, organisation_id: int) -> str | None:
        """Return the booking's appointment code where its organisation takes change requests.

        Only for a confirmed booking: the only kind a change request can name.
        """
        if not booking.is_active or not self._policy(organisation_id).enabled:
            return None
        return appointment_code(self._keys, booking)

    async def request(
        self,
        caller: ApiCaller,
        summary: GroupSummary,
        phone: str,
        code: str,
        start: datetime | None,
    ) -> ChangeRequest:
        """Record a change request: to ``start``, or a cancellation without one.

        The answer is the same whether or not anything matched.

        Raises:
            DomainError: ``change_requests_disabled`` (the organisation has not switched them
                on), ``invalid_contact`` (no valid phone number), ``invalid_appointment_code``
                or ``slot_unavailable`` (the requested time is not free). None of these depends
                on a booking.
        """
        group = summary.group
        policy = self._policy(group.organisation_id)
        if not policy.enabled:
            raise DomainError(ErrorCode.CHANGE_REQUESTS_DISABLED)
        number = normalise_phone(phone, self._phone_region(group.organisation_id), "phone")
        digits = normalise_code(code)
        if digits is None:
            raise DomainError(ErrorCode.INVALID_APPOINTMENT_CODE, "code")
        if start is not None and not await self._free(group, start):
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            candidates = await tx.find_bookings_by_phone(group.id, number)
            # Every candidate's code is checked, so the time does not tell which one matched.
            matches = [b for b in candidates if code_matches(self._keys, b.token_salt, digits)]
            match = next((b for b in matches if _may_change(b, group, start, now)), None)
            if match is not None:  # before the new row, so it replaces only an older one
                await self._ask(tx, match, now)
            stored = await tx.add_change_request(
                ChangeRequest(
                    public_id=new_request_id(),
                    client_id=caller.client.client_id,
                    group_id=group.id,
                    target_start=start,
                    booking_id=match.id if match else None,
                    pinned_start=match.start if match else None,
                    created_at=now,
                    expires_at=request_expiry(now, policy.minutes),
                )
            )
            await tx.add_audit_event(
                AuditEvent(
                    now,
                    caller.client.client_id,
                    ApiScope.BOOKINGS_REQUEST_CHANGE.value,
                    Outcome.SUCCESS,
                    MATCHED if match else NO_MATCH,
                    summary.organisation_slug,
                    group.slug,
                    detail=f"action:{stored.action.value}",
                    booking_id=match.id if match else None,
                )
            )
        return stored

    async def status(self, caller: ApiCaller, public_id: str) -> ChangeRequest:
        """Return the client's own change request.

        Raises:
            DomainError: ``change_request_not_found``, also for another client's request.
        """
        async with self._uow.read() as tx:
            found = await tx.get_change_request(public_id)
        if found is None or found.client_id != caller.client.client_id:
            raise DomainError(ErrorCode.CHANGE_REQUEST_NOT_FOUND)
        return found

    async def open_request(self, ref: PublicRef) -> OpenChange | None:
        """Return the booking behind the link with its open change request, if it has one."""
        details = await self._bookings.find(ref)
        booking = details.booking
        if booking.id is None:  # pragma: no cover - a stored booking has an id
            return None
        async with self._uow.read() as tx:
            request = await tx.get_latest_change_request(booking.id)
        if request is None or not self._open(request, booking, details.group):
            return None
        return OpenChange(details, request)

    async def confirm(self, ref: PublicRef) -> ConfirmedChange:
        """Apply the booking's open change request, as the participant's own cancel or move.

        Raises:
            DomainError: ``change_request_not_found`` when nothing is open any more (also once
                the organisation switched change requests off), or any refusal of the
                participant's cancel or move (``booking_locked``, ``slot_unavailable``, …);
                nothing is changed then.
        """
        details = await self._bookings.find(ref)
        booking_id = details.booking.id
        if booking_id is None:  # pragma: no cover - a stored booking has an id
            raise DomainError(ErrorCode.CHANGE_REQUEST_NOT_FOUND)
        group = details.group
        async with self._uow.locked() as tx:
            booking = await tx.get_booking(booking_id)
            request = await tx.get_latest_change_request(booking_id)
            if booking is None or request is None or not self._open(request, booking, group):
                raise DomainError(ErrorCode.CHANGE_REQUEST_NOT_FOUND)
            confirmed = request.confirm(self._clock.now())
            await tx.save_change_request(confirmed)
            if request.target_start is None:
                cancelled = await self._bookings.cancel_in(tx, booking, group)
                return ConfirmedChange(confirmed, cancelled=cancelled)
            moved = await self._bookings.reschedule_in(tx, booking, group, request.target_start)
            return ConfirmedChange(confirmed, moved=moved)

    def _open(self, request: ChangeRequest, booking: Booking, group: Group) -> bool:
        """Whether the participant may still confirm ``request``: current, and switched on."""
        return (
            booking.is_active
            and request.is_current(booking.start, self._clock.now())
            and self._policy(group.organisation_id).enabled
        )

    async def _ask(self, tx: Transaction, booking: Booking, now: datetime) -> None:
        """Replace the booking's open request, if any, and send the participant the message.

        A message of the replaced request that has not gone out yet is dropped, so the
        participant gets one message per request.
        """
        booking_id = booking.id
        if booking_id is None:  # pragma: no cover - a matched booking is stored
            raise ValueError("booking has not been stored")
        previous = await tx.get_latest_change_request(booking_id)
        if previous is not None and previous.is_current(booking.start, now):
            await tx.save_change_request(previous.supersede(now))
        await tx.delete_pending_jobs(booking_id, {JobType.SEND_CHANGE_REQUEST})
        jobs = plan_for_change_request(now=now, start=booking.start, sms=sms_reachable(booking))
        await tx.add_jobs(booking_id, jobs)

    async def _free(self, group: Group, start: datetime) -> bool:
        """Return whether ``start`` is a free time of the group, as the availability shows it."""
        day = group.today(start)
        return any(
            s.start == start for s in await self._bookings.availability(group.slug, day, day)
        )


def _may_change(booking: Booking, group: Group, start: datetime | None, now: datetime) -> bool:
    """Return whether the participant's own link could make this change now (ADR 0069).

    The same checks as ``BookingService.cancel_in`` and ``reschedule_in``, without raising.
    """
    if not booking.is_active or start == booking.start:
        return False
    check = (
        booking_changes.ensure_participant_may_cancel
        if start is None
        else booking_changes.ensure_participant_may_move
    )
    try:
        check(booking, group, now)
    except DomainError:
        return False
    return True
