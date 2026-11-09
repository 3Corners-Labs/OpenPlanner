"""In-memory ``DigestStore``, ``DigestUnitOfWork`` and ``StaffAddresses`` for the digest's tests.

The store keeps subscriptions, run records and staff in dicts and applies the same rules as the
SQL adapter: a run is unique per subscription and due date, deleting a subscription deletes its
runs, and a listing is filtered by the scope.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from app.domain.authorization import CustomRole, Scope
from app.domain.report_digest import RunStatus, Subscription
from app.domain.staff import StaffFilter, StaffMember


@dataclass
class DigestData:
    """What the fake store holds."""

    subscriptions: dict[int, Subscription] = field(default_factory=dict)
    runs: dict[tuple[int, date], tuple[str, int]] = field(default_factory=dict)
    staff: dict[int, StaffMember] = field(default_factory=dict)
    organisations: dict[str, int] = field(default_factory=dict)
    groups: dict[tuple[int, str], int] = field(default_factory=dict)
    zones: dict[int, str] = field(default_factory=dict)
    locales: dict[int, str] = field(default_factory=dict)
    roles: list[CustomRole] = field(default_factory=list)
    next_id: int = 1


class InMemoryDigestStore:
    """The ``DigestStore`` over one shared ``DigestData``."""

    def __init__(self, data: DigestData) -> None:
        self.data = data

    async def list_subscriptions(
        self, scope: Scope, organisation_slug: str | None = None
    ) -> list[Subscription]:
        return [
            s
            for s in self.data.subscriptions.values()
            if (organisation_slug is None or s.organisation_slug == organisation_slug)
            and (scope.everything or s.organisation_slug in scope.organisations)
        ]

    async def list_all_subscriptions(self) -> list[Subscription]:
        return list(self.data.subscriptions.values())

    async def get_subscription(self, subscription_id: int) -> Subscription | None:
        return self.data.subscriptions.get(subscription_id)

    async def count_subscriptions(self, organisation_id: int) -> int:
        return sum(
            1 for s in self.data.subscriptions.values() if s.organisation_id == organisation_id
        )

    async def organisation_id_of(self, slug: str) -> int | None:
        return self.data.organisations.get(slug)

    async def group_id_of(self, organisation_id: int, slug: str) -> int | None:
        return self.data.groups.get((organisation_id, slug))

    async def add_subscription(self, subscription: Subscription) -> int:
        new_id = self.data.next_id
        self.data.next_id += 1
        self.data.subscriptions[new_id] = replace(subscription, id=new_id)
        return new_id

    async def delete_subscription(self, subscription_id: int) -> bool:
        found = self.data.subscriptions.pop(subscription_id, None)
        self.data.runs = {k: v for k, v in self.data.runs.items() if k[0] != subscription_id}
        return found is not None

    async def claim_digest_run(
        self,
        subscription_id: int,
        due_date: date,
        status: RunStatus,
        sent: int,
        skipped: int,
        at: datetime,
    ) -> bool:
        key = (subscription_id, due_date)
        if key in self.data.runs:
            return False
        self.data.runs[key] = (status.value, skipped)
        return True

    async def has_digest_run(self, subscription_id: int, due_date: date) -> bool:
        return (subscription_id, due_date) in self.data.runs

    async def last_digest_run(self, subscription_id: int) -> tuple[date, str, int] | None:
        found = [(k[1], v) for k, v in self.data.runs.items() if k[0] == subscription_id]
        if not found:
            return None
        day, (status, skipped) = max(found, key=lambda item: item[0])
        return day, status, skipped

    async def get_staff_by_id(self, staff_id: int) -> StaffMember | None:
        return self.data.staff.get(staff_id)

    async def digest_zone(self, organisation_id: int, group_id: int | None) -> str | None:
        return self.data.zones.get(group_id if group_id is not None else organisation_id)

    async def digest_locale(self, organisation_id: int) -> str | None:
        return self.data.locales.get(organisation_id)

    async def list_staff(self, scope: Scope, narrow: StaffFilter) -> list[StaffMember]:
        return list(self.data.staff.values())

    async def get_custom_roles(self) -> list[CustomRole]:
        return list(self.data.roles)


class InMemoryDigestUnitOfWork:
    """The ``DigestUnitOfWork`` over one shared ``DigestData``."""

    def __init__(self, data: DigestData | None = None) -> None:
        self.data = data or DigestData()

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[InMemoryDigestStore]:
        yield InMemoryDigestStore(self.data)

    @asynccontextmanager
    async def read(self) -> AsyncIterator[InMemoryDigestStore]:
        yield InMemoryDigestStore(self.data)


class FakeStaffAddresses:
    """``StaffAddresses`` over a dict of subject to address."""

    def __init__(self, found: dict[str, str] | None = None) -> None:
        self.found = found or {}

    async def address_of(self, subject: str) -> str | None:
        return self.found.get(subject)
