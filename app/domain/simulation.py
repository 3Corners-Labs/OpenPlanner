"""Simulated platform events for trying OpenPlanner without Webex agents (never in production).

The simulator on an admin booking page plays the events Webex and WxCC would send: an agent
accepts the work item, host and participant join, the meeting ends. The admin portal signs them
and posts them to OpenPlanner's own webhook endpoints, so they take the real path. This
service holds the rules around it:

    prepare(principal, slug, id)        stand-in meeting and task ids for a booking without
                                        them; real ids are never replaced
    ids(principal, slug, id)            the ids an event must carry
    record_event(principal, slug, id, name)  audit one played event

Only whoever may edit the group (``group.edit``) may simulate, the booking must belong to the
group, and the composition root switches the service off in production (``enabled``).
"""

from dataclasses import dataclass

from app.domain.admin import AdminService, require
from app.domain.audit import Outcome
from app.domain.authorization import Permission, Principal
from app.domain.booking import Booking, MeetingRef
from app.domain.calls import EventKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import IntegrationService
from app.domain.ports import Clock, Transaction, UnitOfWork

SIMULATED_LINK = "https://simulated-meeting.invalid/join"
"""Join link of a stand-in meeting: ``.invalid`` never resolves (RFC 2606)."""

SIMULATED_DETAIL = "simulated"
"""Timeline detail of the stand-in meeting and task, so nobody mistakes them for real ones."""

PREPARE_EVENT = "simulator.prepare"
PLAY_EVENT = "simulator.event"


@dataclass(frozen=True)
class SimulationIds:
    """The platform ids a booking's events carry; ``None`` when it has none yet."""

    meeting_id: str | None
    task_id: str | None


class SimulationService:
    """Application service behind the simulator buttons.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises and audits.
        integration: Records the stand-ins on the booking's timeline.
        enabled: ``False`` in production: every call is refused.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        integration: IntegrationService,
        *,
        enabled: bool,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._integration = integration
        self.enabled = enabled

    async def prepare(self, principal: Principal, slug: str, booking_id: int) -> SimulationIds:
        """Give the booking stand-in ids where it has none; return the ids it now has.

        Raises:
            DomainError: ``not_permitted`` (also when switched off) or ``booking_not_found``.
        """
        organisation = await self._authorize(principal, slug)
        created: list[EventKind] = []
        async with self._uow.locked() as tx:
            booking = await _booking(tx, slug, booking_id)
            meeting_id = booking.meeting.id if booking.meeting else None
            if meeting_id is None:
                meeting_id = f"simulated-meeting-{booking_id}"
                await tx.save_meeting(booking_id, MeetingRef(meeting_id, SIMULATED_LINK))
                created.append(EventKind.MEETING_CREATED)
            task_id = booking.wxcc_task_id
            if task_id is None:
                task_id = f"simulated-task-{booking_id}"
                await tx.save_task_id(booking_id, task_id)
                created.append(EventKind.TASK_CREATED)
        for kind in created:  # so status and timeline read as after a real dispatch
            await self._integration.record(booking_id, kind, detail=SIMULATED_DETAIL)
        await self._admin.record(
            principal.subject, PREPARE_EVENT, Outcome.SUCCESS, "", organisation, slug
        )
        return SimulationIds(meeting_id, task_id)

    async def ids(self, principal: Principal, slug: str, booking_id: int) -> SimulationIds:
        """Return the meeting and task ids the booking's events carry.

        Raises:
            DomainError: ``not_permitted`` (also when switched off) or ``booking_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            booking = await _booking(tx, slug, booking_id)
        return SimulationIds(booking.meeting.id if booking.meeting else None, booking.wxcc_task_id)

    async def record_event(
        self, principal: Principal, slug: str, booking_id: int, name: str = ""
    ) -> None:
        """Audit that ``name`` was played for the booking.

        Raises:
            DomainError: ``not_permitted`` (also when switched off) or ``booking_not_found``.
        """
        organisation = await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            await _booking(tx, slug, booking_id)
        await self._admin.record(
            principal.subject, PLAY_EVENT, Outcome.SUCCESS, name[:64], organisation, slug
        )

    async def _authorize(self, principal: Principal, slug: str) -> str:
        """Require the simulator on and ``group.edit``; return the organisation slug."""
        if not self.enabled:
            raise DomainError(ErrorCode.NOT_PERMITTED)
        authorization = require(
            await self._admin.authorize(principal, Permission.GROUP_EDIT, group_slug=slug)
        )
        organisation = authorization.target.organisation
        if organisation is None:  # pragma: no cover - a group target always has one
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        return organisation


async def _booking(tx: Transaction, slug: str, booking_id: int) -> Booking:
    """Return the booking if it belongs to group ``slug``.

    Raises:
        DomainError: ``booking_not_found`` (also for another group's booking).
    """
    booking = await tx.get_booking(booking_id)
    group = await tx.get_group(slug)
    if booking is None or group is None or booking.group_id != group.id:
        raise DomainError(ErrorCode.BOOKING_NOT_FOUND)
    return booking
