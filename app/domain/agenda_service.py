"""Agenda use cases for admins: a group's slots with agents and shortfalls (ADR 0044).

Reading only; everything needs ``bookings.view`` on the group (the matrix, ADR 0014). The
agenda counts bookings and shows no contact data; the day view adds first names through
``IntegrationService.day_overview``, as before.

    days        bookings.view   group level   slots of up to MAX_AGENDA_DAYS days
    shortfalls  bookings.view   group level   slots short of agents within the horizon
    summaries   bookings.view   every group in scope (dashboard)
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.admin import AdminService, require
from app.domain.agenda import AgendaSlot, agenda_day, first_shortfall
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.availability import utc_bounds
from app.domain.errors import DomainError, ErrorCode
from app.domain.holiday_service import effective_schedule
from app.domain.lookups import group_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, windows_on

MAX_AGENDA_DAYS = 31
"""Largest number of days one agenda request may cover (a week view uses 7)."""


@dataclass(frozen=True)
class AgendaDay:
    """The slots of one local day."""

    day: date
    slots: list[AgendaSlot]


@dataclass(frozen=True)
class ShortfallSummary:
    """How many slots ahead are short of agents, and the first of them."""

    count: int
    first: AgendaSlot | None
    horizon_days: int


class AgendaService:
    """Application service for a group's agenda.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises every operation and audits refusals.
        horizon_days: How far ahead the shortfall summary looks (``shortfall_horizon_days``).
    """

    def __init__(
        self, uow: UnitOfWork, clock: Clock, admin: AdminService, horizon_days: int
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._horizon_days = horizon_days

    async def days(
        self, principal: Principal, slug: str, first_day: date, days: int
    ) -> list[AgendaDay]:
        """Return the group's slots from ``first_day`` (group time) for ``days`` days.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``invalid_range``.
        """
        await self._authorize(principal, slug)
        if not 1 <= days <= MAX_AGENDA_DAYS:
            raise DomainError(ErrorCode.INVALID_RANGE)
        async with self._uow.read() as tx:
            group = await group_by_slug(tx, slug)
            return await agenda_days(tx, group, first_day, days, self._clock.now())

    async def shortfalls(self, principal: Principal, slug: str) -> ShortfallSummary:
        """Return the group's slots short of agents from now to the horizon.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            return await self._summary(tx, await group_by_slug(tx, slug))

    async def summaries(self, principal: Principal) -> dict[str, ShortfallSummary]:
        """Return the shortfall summary of every group whose bookings ``principal`` may view."""
        async with self._uow.read() as tx:
            groups = await tx.list_groups(scope_for(principal, Permission.BOOKINGS_VIEW))
            return {g.group.slug: await self._summary(tx, g.group) for g in groups}

    async def _summary(self, tx: Transaction, group: Group) -> ShortfallSummary:
        """Return ``group``'s shortfall summary at the current time."""
        now = self._clock.now()
        days = await agenda_days(tx, group, group.today(now), self._horizon_days, now)
        short = [s for d in days for s in d.slots if s.open_shortfall and s.start >= now]
        return ShortfallSummary(len(short), first_shortfall(short, now), self._horizon_days)

    async def _authorize(self, principal: Principal, slug: str) -> None:
        """Require ``bookings.view`` on the group."""
        require(await self._admin.authorize(principal, Permission.BOOKINGS_VIEW, group_slug=slug))


async def agenda_days(
    tx: Transaction, group: Group, first_day: date, days: int, now: datetime
) -> list[AgendaDay]:
    """Return ``days`` agenda days of ``group`` from ``first_day``."""
    last_day = first_day + timedelta(days=days - 1)
    rules, overrides = await effective_schedule(tx, group, first_day, last_day)
    lower, upper = utc_bounds(group, first_day, last_day)
    booked = Counter(b.start for b in await tx.find_confirmed_bookings(group.id, lower, upper))
    closures = await tx.get_closures(group.id, now)
    accepted = {a.start: a for a in await tx.get_shortfall_acceptances(group.id, lower, upper)}
    result = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        windows = windows_on(day, rules, overrides)
        slots = agenda_day(group, day, windows, booked, closures, accepted)
        result.append(AgendaDay(day, slots))
    return result
