"""SQL for requests, work items and the request-level personal data (ADR 0121 step 5).

A mixin of ``SqlTransaction``. The methods carry no scope: a request is reached through its
booking, which the caller found with a scoped lookup, and the worker's rounds act on every
organisation. Updates are conditional on the state the caller read (``seen``), so a competing
transaction wins cleanly and the loser writes nothing; the claim and the watchdog run inside the
write lock of ``locked()`` all the same. The partial unique index ``uq_work_items_active`` keeps
one active work item per request whatever a caller does.

``open_occupations`` is the part of ``get_pool_commitments`` that the work items add: a started
work item that has not ended holds one unit of its pool, keyed like the booking's hold, and so
does claimed immediate work from its claim on (ADR 0121 step 7). The immediate-work queries
(``get_request_by_ref``, ``waiting_immediate``, ...) carry no scope either: the participant's
page authorises by its cookie, the staff use cases by the service they authorised.
"""

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.adapters.db.form_fields import answers_from_json, answers_json
from app.adapters.db.models import (
    BookingRow,
    GroupRow,
    RequestPersonalDataRow,
    ServiceRequestRow,
    SlotRow,
    WorkItemRow,
)
from app.domain.booking import BookingStatus
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef
from app.domain.pool_accounting import Commitment, CommitmentKey
from app.domain.service_flows import IntakeMode
from app.domain.service_requests import (
    ACTIVE_ITEM,
    IN_DOUBT,
    TERMINAL,
    HandlingState,
    PersonalDataState,
    RequestPersonalData,
    ServiceRequest,
    UnclaimedBooking,
    WorkChannel,
    WorkItem,
    WorkItemState,
)

_ACTIVE = sorted(state.value for state in ACTIVE_ITEM)
"""The stored values of an active work item; ``models.ACTIVE_WORK_ITEM`` is the index's twin
(a test keeps the two equal)."""

_WAITING = sorted(state.value for state in (HandlingState.QUEUED, HandlingState.ASSIGNED))
"""The handling states of the line ``max_waiting`` bounds (ADR 0121 step 7)."""

_TERMINAL = sorted(state.value for state in TERMINAL)

_WATCHED = sorted(
    state.value
    for state in HandlingState
    if state not in TERMINAL and state is not HandlingState.RECOVERY_REQUIRED
)
"""The handling states the watchdog round still looks at."""


def request_of(row: ServiceRequestRow) -> ServiceRequest:
    """Map a request row."""
    return ServiceRequest(
        id=row.id,
        organisation_id=row.organisation_id,
        group_id=row.group_id,
        flow=FlowRef(row.flow_id, row.flow_revision),
        intake=IntakeMode(row.intake),
        booking_id=row.booking_id,
        handling=HandlingState(row.handling),
        created_at=row.created_at,
        watchdog_at=row.watchdog_at,
        resolved_at=row.resolved_at,
        personal_data=PersonalDataState(row.personal_data),
        data_deleted_at=row.data_deleted_at,
        pool_id=row.pool_id,
        subject=row.subject,
        arrived_at=row.arrived_at,
        public_ref=row.public_ref,
        access_hash=row.access_hash,
        intake_key_hash=row.intake_key_hash,
    )


def item_of(row: WorkItemRow) -> WorkItem:
    """Map a work item row."""
    return WorkItem(
        id=row.id,
        request_id=row.request_id,
        state=WorkItemState(row.state),
        assigned_to=row.assigned_to,
        assigned_at=row.assigned_at,
        lease_until=row.lease_until,
        started_at=row.started_at,
        pool_id=row.pool_id,
        planned_until=row.planned_until,
        start_conflict=row.start_conflict,
        ended_at=row.ended_at,
        channel=WorkChannel(row.channel),
    )


def _item_values(item: WorkItem) -> dict[str, object]:
    """Return the columns a work item writes."""
    return {
        "state": item.state.value,
        "assigned_to": item.assigned_to,
        "assigned_at": item.assigned_at,
        "lease_until": item.lease_until,
        "started_at": item.started_at,
        "pool_id": item.pool_id,
        "planned_until": item.planned_until,
        "start_conflict": item.start_conflict,
        "ended_at": item.ended_at,
        "channel": item.channel.value,
    }


async def open_occupations(
    session: AsyncSession, pool_id: int, upper: datetime
) -> list[Commitment]:
    """Return the units of the pool held by started work items that have not ended.

    Bounded by the open work items of one pool (``ix_work_items_pool_open``), not by ``lower``:
    an occupation past its planned end is an overrun and lasts until it ends.
    """
    found = await session.scalars(
        select(WorkItemRow)
        .where(
            WorkItemRow.pool_id == pool_id,
            WorkItemRow.ended_at.is_(None),
            WorkItemRow.planned_until.is_not(None),
            or_(
                WorkItemRow.started_at < upper,
                and_(
                    WorkItemRow.started_at.is_(None),
                    WorkItemRow.state == WorkItemState.ASSIGNED.value,
                    WorkItemRow.assigned_at < upper,
                ),
            ),
        )
        .order_by(func.coalesce(WorkItemRow.started_at, WorkItemRow.assigned_at), WorkItemRow.id)
    )
    items = list(found)
    keys = await _keys(session, {item.request_id for item in items})
    held = (item_of(item).commitment(keys[item.request_id]) for item in items)
    return [commitment for commitment in held if commitment is not None]


async def _keys(session: AsyncSession, request_ids: set[int]) -> dict[int, CommitmentKey]:
    """Return the commitment key of each request: its booking's, or its own without one."""
    if not request_ids:
        return {}
    rows = await session.execute(
        select(ServiceRequestRow.id, ServiceRequestRow.booking_id).where(
            ServiceRequestRow.id.in_(request_ids)
        )
    )
    return {
        request_id: ("booking", booking_id) if booking_id is not None else ("request", request_id)
        for request_id, booking_id in rows
    }


class ServiceRequestQueries:
    """The requests' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def get_request_of_booking(self, booking_id: int) -> ServiceRequest | None:
        """Return the request of a booking, or ``None``."""
        row = await self._session.scalar(
            select(ServiceRequestRow).where(ServiceRequestRow.booking_id == booking_id)
        )
        return request_of(row) if row is not None else None

    async def get_service_request(self, request_id: int) -> ServiceRequest | None:
        """Return a request by id, or ``None``."""
        row = await self._session.get(ServiceRequestRow, request_id)
        return request_of(row) if row is not None else None

    async def open_request_ids(self) -> list[int]:
        """Return the ids of the requests the watchdog still watches."""
        found = await self._session.scalars(
            select(ServiceRequestRow.id)
            .where(ServiceRequestRow.handling.in_(_WATCHED))
            .order_by(ServiceRequestRow.id)
        )
        return list(found)

    async def requests_in_recovery(self, group_id: int | None) -> list[ServiceRequest]:
        """Return the requests in recovery, of service ``group_id`` or of all, in id order."""
        query = select(ServiceRequestRow).where(
            ServiceRequestRow.handling == HandlingState.RECOVERY_REQUIRED.value
        )
        if group_id is not None:
            query = query.where(ServiceRequestRow.group_id == group_id)
        rows = await self._session.scalars(query.order_by(ServiceRequestRow.id))
        return [request_of(row) for row in rows]

    async def unclaimed_pool_bookings(self, ended_before: datetime) -> list[UnclaimedBooking]:
        """Return confirmed pool bookings with a flow, ended before the moment, without request."""
        found = await self._session.execute(
            select(
                BookingRow.id,
                GroupRow.organisation_id,
                BookingRow.group_id,
                BookingRow.flow_id,
                BookingRow.flow_revision,
                SlotRow.end,
            )
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .join(GroupRow, GroupRow.id == BookingRow.group_id)
            .outerjoin(ServiceRequestRow, ServiceRequestRow.booking_id == BookingRow.id)
            .where(
                SlotRow.capacity_pool_id.is_not(None),
                SlotRow.end < ended_before,
                BookingRow.status == BookingStatus.CONFIRMED.value,
                BookingRow.flow_id.is_not(None),
                BookingRow.flow_revision.is_not(None),
                ServiceRequestRow.id.is_(None),
            )
            .order_by(BookingRow.id)
        )
        return [
            UnclaimedBooking(booking_id, organisation_id, group_id, FlowRef(flow, revision), end)
            for booking_id, organisation_id, group_id, flow, revision, end in found
            if flow is not None and revision is not None
        ]

    async def requests_holding_data(
        self, group_ids: Collection[int] | None = None
    ) -> list[ServiceRequest]:
        """Return the requests without a booking that still hold personal data.

        A request-level record, or an agent's pseudonym on one of its work items: a flow that
        collected nothing still names who took the work.
        """
        pseudonym = (
            select(WorkItemRow.id)
            .where(
                WorkItemRow.request_id == ServiceRequestRow.id,
                WorkItemRow.assigned_to.is_not(None),
            )
            .exists()
        )
        found = await self._session.scalars(
            select(ServiceRequestRow)
            .where(
                ServiceRequestRow.booking_id.is_(None),
                or_(
                    ServiceRequestRow.personal_data == PersonalDataState.PRESENT.value,
                    pseudonym,
                ),
                true() if group_ids is None else ServiceRequestRow.group_id.in_(group_ids),
            )
            .order_by(ServiceRequestRow.id)
        )
        return [request_of(row) for row in found]

    async def get_active_work_item(self, request_id: int) -> WorkItem | None:
        """Return the request's one active work item, or ``None``."""
        row = await self._session.scalar(
            select(WorkItemRow).where(
                WorkItemRow.request_id == request_id, WorkItemRow.state.in_(_ACTIVE)
            )
        )
        return item_of(row) if row is not None else None

    async def work_items_of(self, request_id: int) -> list[WorkItem]:
        """Return every work item of a request, oldest first."""
        rows = await self._session.scalars(
            select(WorkItemRow).where(WorkItemRow.request_id == request_id).order_by(WorkItemRow.id)
        )
        return [item_of(row) for row in rows]

    async def work_items_in_doubt(self) -> list[WorkItem]:
        """Return the contact-center attempts whose create's outcome is unknown, oldest first."""
        rows = await self._session.scalars(
            select(WorkItemRow)
            .where(
                WorkItemRow.channel == WorkChannel.CONTACT_CENTER.value,
                WorkItemRow.state.in_(sorted(state.value for state in IN_DOUBT)),
            )
            .order_by(WorkItemRow.id)
        )
        return [item_of(row) for row in rows]

    async def add_service_request(
        self, request: ServiceRequest, data: RequestPersonalData | None = None
    ) -> ServiceRequest:
        """Store a new request (and its record, when it holds something); return it with its id.

        Raises:
            DomainError: ``invalid_configuration`` (field ``data``) for a record on a request
                with a booking: its data stays on the booking (D1).
        """
        keep = data is not None and not data.empty
        if keep and request.booking_id is not None:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "data")
        state = PersonalDataState.PRESENT if keep else request.personal_data
        row = ServiceRequestRow(
            organisation_id=request.organisation_id,
            group_id=request.group_id,
            flow_id=request.flow.flow_id,
            flow_revision=request.flow.revision,
            intake=request.intake.value,
            booking_id=request.booking_id,
            handling=request.handling.value,
            created_at=request.created_at,
            watchdog_at=request.watchdog_at,
            resolved_at=request.resolved_at,
            personal_data=state.value,
            data_deleted_at=request.data_deleted_at,
            pool_id=request.pool_id,
            subject=request.subject,
            arrived_at=request.arrived_at,
            public_ref=request.public_ref,
            access_hash=request.access_hash,
            intake_key_hash=request.intake_key_hash,
        )
        self._session.add(row)
        await self._session.flush()
        if keep and data is not None:
            self._session.add(
                RequestPersonalDataRow(
                    request_id=row.id,
                    first_name=data.first_name,
                    last_name=data.last_name,
                    email=data.email,
                    phone=data.phone,
                    form_answers=answers_json(data.answers),
                )
            )
            await self._session.flush()
        return request_of(row)

    async def add_work_item(self, item: WorkItem) -> WorkItem:
        """Store a new work item and return it with its id."""
        row = WorkItemRow(request_id=item.request_id, **_item_values(item))
        self._session.add(row)
        await self._session.flush()
        return item_of(row)

    async def update_service_request(self, request: ServiceRequest, seen: HandlingState) -> bool:
        """Write handling, ``resolved_at`` and ``watchdog_at`` if the handling is still ``seen``."""
        result = await self._session.execute(
            update(ServiceRequestRow)
            .where(ServiceRequestRow.id == request.id, ServiceRequestRow.handling == seen.value)
            .values(
                handling=request.handling.value,
                resolved_at=request.resolved_at,
                watchdog_at=request.watchdog_at,
                arrived_at=request.arrived_at,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def get_request_by_ref(self, public_ref: str) -> ServiceRequest | None:
        """Return the immediate request with this public reference, or ``None``."""
        row = await self._session.scalar(
            select(ServiceRequestRow).where(ServiceRequestRow.public_ref == public_ref)
        )
        return request_of(row) if row is not None else None

    async def get_request_by_intake_key(
        self, group_id: int, intake_key_hash: str
    ) -> ServiceRequest | None:
        """Return the service's immediate request made with this intake key, or ``None``."""
        row = await self._session.scalar(
            select(ServiceRequestRow).where(
                ServiceRequestRow.group_id == group_id,
                ServiceRequestRow.intake_key_hash == intake_key_hash,
            )
        )
        return request_of(row) if row is not None else None

    async def set_request_access(self, request_id: int, access_hash: str) -> bool:
        """Replace the cookie's hash while the request still has one (not after deletion)."""
        result = await self._session.execute(
            update(ServiceRequestRow)
            .where(ServiceRequestRow.id == request_id, ServiceRequestRow.access_hash.is_not(None))
            .values(access_hash=access_hash)
        )
        return bool(getattr(result, "rowcount", 0))

    async def waiting_immediate(self, pool_id: int) -> list[ServiceRequest]:
        """Return the pool's queued and assigned immediate requests, oldest first."""
        return await self._immediate(
            ServiceRequestRow.pool_id == pool_id,
            ServiceRequestRow.handling.in_(_WAITING),
        )

    async def open_immediate(self, group_id: int) -> list[ServiceRequest]:
        """Return the service's immediate requests that are not finished, oldest first."""
        return await self._immediate(
            ServiceRequestRow.group_id == group_id,
            ServiceRequestRow.handling.not_in(_TERMINAL),
        )

    async def _immediate(self, *where: ColumnElement[bool]) -> list[ServiceRequest]:
        """Return the immediate requests matching ``where``, oldest first."""
        rows = await self._session.scalars(
            select(ServiceRequestRow)
            .where(ServiceRequestRow.intake == IntakeMode.ON_DEMAND.value, *where)
            .order_by(ServiceRequestRow.created_at, ServiceRequestRow.id)
        )
        return [request_of(row) for row in rows]

    async def update_work_item(self, item: WorkItem, seen: WorkItemState) -> bool:
        """Write the work item if its stored state is still ``seen``."""
        values = _item_values(item)
        del values["assigned_to"], values["assigned_at"], values["channel"]  # fixed at the claim
        result = await self._session.execute(
            update(WorkItemRow)
            .where(WorkItemRow.id == item.id, WorkItemRow.state == seen.value)
            .values(values)
        )
        return bool(getattr(result, "rowcount", 0))

    async def get_request_personal_data(self, request_id: int) -> RequestPersonalData | None:
        """Return the request-level record, or ``None``."""
        row = await self._session.get(RequestPersonalDataRow, request_id)
        if row is None:
            return None
        return RequestPersonalData(
            row.first_name,
            row.last_name,
            row.email,
            row.phone,
            answers_from_json(row.form_answers),
        )

    async def delete_request_personal_data(self, request_id: int, now: datetime) -> bool:
        """Empty the pseudonyms; delete the record's row and mark the request ``deleted``.

        Returns whether anything was removed (a record or a pseudonym): idempotent.
        """
        agents = await self._session.execute(
            update(WorkItemRow)
            .where(WorkItemRow.request_id == request_id, WorkItemRow.assigned_to.is_not(None))
            .values(assigned_to=None)
        )
        result = await self._session.execute(
            delete(RequestPersonalDataRow).where(RequestPersonalDataRow.request_id == request_id)
        )
        access = await self._session.execute(
            update(ServiceRequestRow)
            .where(
                ServiceRequestRow.id == request_id,
                or_(
                    ServiceRequestRow.access_hash.is_not(None),
                    ServiceRequestRow.intake_key_hash.is_not(None),
                ),
            )
            .values(access_hash=None, intake_key_hash=None)
        )
        if getattr(result, "rowcount", 0):
            await self._session.execute(
                update(ServiceRequestRow)
                .where(ServiceRequestRow.id == request_id)
                .values(personal_data=PersonalDataState.DELETED.value, data_deleted_at=now)
            )
            return True
        return bool(getattr(agents, "rowcount", 0) or getattr(access, "rowcount", 0))
