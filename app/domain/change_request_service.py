"""Use cases of a bot's change request and the participant's confirmation (ADR 0023 step 2).

    bot (machine API, scope bookings:request_change on the group)
        request(caller, group, phone, code, start)
            ─► switched on? code and phone well-formed? a free time of the service? ─► one
               locked transaction: the group's bookings with that phone number, the one whose
               code matches, that the participant could still change and that could move to the
               time (on its own slot: its pool, or the service's own times) ─► request row
               (with or without a booking),
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
from datetime import datetime, timedelta

from app.domain import booking_changes
from app.domain.api_access import client_may_act
from app.domain.api_clients import ApiCaller
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import ApiScope, Target
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
COOLDOWN = "cooldown"
"""Audit reasons of a change request: for the superuser's log, never in the API's answer.

``cooldown``: the pair matched, but the booking had a request less than
``agent_change_request_cooldown_minutes`` ago, so nothing was sent (step 2b)."""


@dataclass(frozen=True)
class ChangePolicy:
    """An organisation's settings for change requests (class D, ADR 0023 step 2).

    Attributes:
        enabled: ``agent_change_requests_enabled``.
        minutes: ``agent_change_request_minutes``: how long the participant can confirm.
        cooldown_minutes: ``agent_change_request_cooldown_minutes`` (step 2b): after a
            request reached a booking, how long a new one for it sends nothing.
    """

    enabled: bool = False
    minutes: int = 60
    cooldown_minutes: int = 10


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

        The answer is the same whether or not anything matched. A time is first judged on
        every placement of the service (its own times and the pools of its flows), before any
        booking is read; the booking that matches is then judged on its own slot, and one that
        could not move there is recorded as no match, like one past its change deadline
        (ADR 0122 step 5, gate item G04).

        Raises:
            DomainError: ``change_requests_disabled`` (the organisation has not switched them
                on), ``invalid_contact`` (no valid phone number), ``invalid_appointment_code``
                or ``slot_unavailable`` (the requested time is not free anywhere in the
                service). None of these depends on a booking.
        """
        group = summary.group
        policy = self._policy(group.organisation_id)
        if not policy.enabled:
            raise DomainError(ErrorCode.CHANGE_REQUESTS_DISABLED)
        number = normalise_phone(phone, self._phone_region(group.organisation_id), "phone")
        digits = normalise_code(code)
        if digits is None:
            raise DomainError(ErrorCode.INVALID_APPOINTMENT_CODE, "code")
        if start is not None and not await self._bookings.offers_somewhere(group, start):
            raise DomainError(ErrorCode.SLOT_UNAVAILABLE)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            candidates = await tx.find_bookings_by_phone(group.id, number)
            # Every candidate's code is checked, so the time does not tell which one matched.
            matches = [b for b in candidates if code_matches(self._keys, b.token_salt, digits)]
            match = await self._changeable(tx, matches, group, start, now)
            reason = MATCHED if match else NO_MATCH
            previous = await tx.get_latest_change_request(match.id) if match and match.id else None
            target = match
            if previous is not None and previous.cools_down(now, policy.cooldown_minutes):
                target, reason = None, COOLDOWN  # recorded as no match: nothing sent or replaced
            if target is not None:  # before the new row, so it replaces only an older one
                await self._ask(tx, target, previous, now)
            stored = await tx.add_change_request(
                ChangeRequest(
                    public_id=new_request_id(),
                    client_id=caller.client.client_id,
                    group_id=group.id,
                    target_start=start,
                    booking_id=target.id if target else None,
                    pinned_start=target.start if target else None,
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
                    reason,
                    summary.organisation_slug,
                    group.slug,
                    detail=f"action:{stored.action.value}",
                    # Also for a cooldown: only the superuser's log names the booking.
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
            if request is None or not await self._open(tx, request, booking, details.group):
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
            if (
                booking is None
                or request is None
                or not await self._open(tx, request, booking, group)
            ):
                raise DomainError(ErrorCode.CHANGE_REQUEST_NOT_FOUND)
            confirmed = request.confirm(self._clock.now())
            await tx.save_change_request(confirmed)
            if request.target_start is None:
                cancelled = await self._bookings.cancel_in(tx, booking, group)
                return ConfirmedChange(confirmed, cancelled=cancelled)
            moved = await self._bookings.reschedule_in(tx, booking, group, request.target_start)
            return ConfirmedChange(confirmed, moved=moved)

    async def _open(
        self, tx: Transaction, request: ChangeRequest, booking: Booking, group: Group
    ) -> bool:
        """Whether the participant may still confirm ``request`` (``request_still_open``)."""
        enabled = self._policy(group.organisation_id).enabled
        return await request_still_open(tx, request, booking, group, enabled, self._clock.now())

    async def _ask(
        self,
        tx: Transaction,
        booking: Booking,
        previous: ChangeRequest | None,
        now: datetime,
    ) -> None:
        """Replace the booking's open request, if any, and send the participant the message.

        A message of the replaced request that has not gone out yet is dropped, so the
        participant gets one message per request.
        """
        booking_id = booking.id
        if booking_id is None:  # pragma: no cover - a matched booking is stored
            raise ValueError("booking has not been stored")
        if previous is not None and previous.is_current(booking.start, now):
            await tx.save_change_request(previous.supersede(now))
        await tx.delete_pending_jobs(booking_id, {JobType.SEND_CHANGE_REQUEST})
        jobs = plan_for_change_request(now=now, start=booking.start, sms=sms_reachable(booking))
        await tx.add_jobs(booking_id, jobs)

    async def _changeable(
        self,
        tx: Transaction,
        matches: list[Booking],
        group: Group,
        start: datetime | None,
        now: datetime,
    ) -> Booking | None:
        """Return the first match the participant could change now, to ``start`` if given.

        A move is judged on the booking's own slot (``BookingService.may_move_to``), so a
        booking on a pool is asked only about a free time of its pool.
        """
        for booking in matches:
            if not _may_change(booking, group, start, now):
                continue
            if start is None or await self._bookings.may_move_to(tx, booking, group, start):
                return booking
        return None


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


async def request_still_open(
    tx: Transaction,
    request: ChangeRequest,
    booking: Booking,
    group: Group,
    enabled: bool,
    now: datetime,
) -> bool:
    """Return whether ``request`` can still be confirmed, or its message still sent.

    The one rule for the confirm page, the confirmation and the message job: the booking goes
    ahead, the request is current (``ChangeRequest.is_current``), the organisation takes change
    requests (``enabled``: ``agent_change_requests_enabled``, only where the deployment runs the
    API), and the client that asked may still ask (step 2b): enabled, still granted
    ``bookings:request_change`` on the group, with the superuser's API switch on.
    """
    if not (enabled and booking.is_active and request.is_current(booking.start, now)):
        return False
    client = await tx.get_api_client(request.client_id)
    organisation = await tx.get_organisation_by_id(group.organisation_id)
    if organisation is None:
        return False
    target = Target.for_group(group.slug, organisation.slug)
    # The deployment's switch is part of ``enabled``; here the superuser's and the client's.
    settings = await tx.get_api_settings()
    return client_may_act(settings, client, ApiScope.BOOKINGS_REQUEST_CHANGE, target)


async def prune_change_requests(uow: UnitOfWork, clock: Clock, keep: timedelta) -> int:
    """Delete change requests that expired more than ``keep`` ago; return how many (step 2b).

    ``keep`` is ``agent_change_request_retention_days``. Matched or not, a request has no use
    after that: its status is then ``change_request_not_found``; the audit events stay.
    """
    async with uow.locked() as tx:
        return await tx.delete_change_requests_expired_before(clock.now() - keep)
