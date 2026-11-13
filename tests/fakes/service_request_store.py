"""In-memory requests, work items and request-level personal data (ADR 0121 step 5).

A mixin of ``InMemoryTransaction`` on top of the pool staffing store. It stores, and keeps the two
rules the database keeps itself: one request per booking and one active work item per request;
the updates are conditional on the state read, as in SQL.
``tests/adapters/db/test_service_requests.py`` runs the same cases on both. Its data lives in
the fake's ``State`` (``requests``, ``work_items``, ``request_data``).
"""

from dataclasses import replace
from datetime import datetime

from app.domain.booking import BookingStatus
from app.domain.errors import DomainError, ErrorCode
from app.domain.pool_accounting import Commitment
from app.domain.service_requests import (
    HandlingState,
    PersonalDataState,
    RequestPersonalData,
    ServiceRequest,
    UnclaimedBooking,
    WorkItem,
    WorkItemState,
)
from tests.fakes.pool_staffing_store import PoolStaffingStore


class ServiceRequestStore(PoolStaffingStore):
    """The requests' part of the in-memory transaction."""

    async def get_pool_commitments(
        self, pool_id: int, lower: datetime, upper: datetime
    ) -> list[Commitment]:
        held = await super().get_pool_commitments(pool_id, lower, upper)
        started = [i for i in self.state.work_items.values() if i.started_at is not None]
        for item in sorted(started, key=lambda i: (i.started_at, i.id)):
            request = self.state.requests[item.request_id]
            found = item.commitment(request.key) if item.pool_id == pool_id else None
            if found is not None and found.start < upper:
                held.append(found)
        return held

    async def get_request_of_booking(self, booking_id: int) -> ServiceRequest | None:
        return next((r for r in self.state.requests.values() if r.booking_id == booking_id), None)

    async def get_service_request(self, request_id: int) -> ServiceRequest | None:
        return self.state.requests.get(request_id)

    async def open_request_ids(self) -> list[int]:
        return sorted(
            key
            for key, request in self.state.requests.items()
            if not request.terminal and request.handling is not HandlingState.RECOVERY_REQUIRED
        )

    async def unclaimed_pool_bookings(self, ended_before: datetime) -> list[UnclaimedBooking]:
        found = []
        for booking_id, booking in sorted(self.state.bookings.items()):
            slot = self.state.slots.get(booking.slot_id)
            group = self.state.groups.get(booking.group_id)
            if (
                slot is None
                or group is None
                or slot.capacity_pool_id is None
                or booking.flow is None
                or booking.status is not BookingStatus.CONFIRMED
                or not slot.end < ended_before
                or await self.get_request_of_booking(booking_id) is not None
            ):
                continue
            found.append(
                UnclaimedBooking(
                    booking_id, group.organisation_id, group.id, booking.flow, slot.end
                )
            )
        return found

    async def requests_holding_data(self) -> list[ServiceRequest]:
        return [
            r
            for _, r in sorted(self.state.requests.items())
            if r.personal_data is PersonalDataState.PRESENT
        ]

    async def get_active_work_item(self, request_id: int) -> WorkItem | None:
        return next(
            (i for i in self.state.work_items.values() if i.request_id == request_id and i.active),
            None,
        )

    async def add_service_request(
        self, request: ServiceRequest, data: RequestPersonalData | None = None
    ) -> ServiceRequest:
        keep = data is not None and not data.empty
        if keep and request.booking_id is not None:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "data")
        if request.booking_id is not None and await self.get_request_of_booking(request.booking_id):
            raise ValueError("UNIQUE constraint failed: service_requests.booking_id")
        stored = replace(
            request,
            id=self._new_id(),
            personal_data=PersonalDataState.PRESENT if keep else request.personal_data,
        )
        self.state.requests[stored.id or 0] = stored
        if keep and data is not None:
            self.state.request_data[stored.id or 0] = data
        return stored

    async def add_work_item(self, item: WorkItem) -> WorkItem:
        if item.active and await self.get_active_work_item(item.request_id):
            raise ValueError("UNIQUE constraint failed: work_items.request_id")
        stored = replace(item, id=self._new_id())
        self.state.work_items[stored.id or 0] = stored
        return stored

    async def update_service_request(self, request: ServiceRequest, seen: HandlingState) -> bool:
        stored = self.state.requests.get(request.id or 0)
        if stored is None or stored.handling is not seen:
            return False
        self.state.requests[request.id or 0] = replace(
            stored, handling=request.handling, resolved_at=request.resolved_at
        )
        return True

    async def update_work_item(self, item: WorkItem, seen: WorkItemState) -> bool:
        stored = self.state.work_items.get(item.id or 0)
        if stored is None or stored.state is not seen:
            return False
        self.state.work_items[item.id or 0] = replace(
            item, assigned_to=stored.assigned_to, assigned_at=stored.assigned_at
        )
        return True

    async def get_request_personal_data(self, request_id: int) -> RequestPersonalData | None:
        return self.state.request_data.get(request_id)

    async def delete_request_personal_data(self, request_id: int, now: datetime) -> bool:
        if self.state.request_data.pop(request_id, None) is None:
            return False
        self.state.requests[request_id] = replace(
            self.state.requests[request_id],
            personal_data=PersonalDataState.DELETED,
            data_deleted_at=now,
        )
        self._forget_agents(request_id)
        return True

    def _forget_agents(self, request_id: int) -> None:
        """Empty the pseudonym on the request's work items, as the SQL adapter does."""
        for key, item in self.state.work_items.items():
            if item.request_id == request_id:
                self.state.work_items[key] = replace(item, assigned_to=None)

    def _forget_agents_of_booking(self, booking_id: int) -> None:
        """Empty the pseudonyms of the booking's request (the booking's purge, ADR 0121 step 5)."""
        for request in self.state.requests.values():
            if request.booking_id == booking_id:
                self._forget_agents(request.id or 0)
