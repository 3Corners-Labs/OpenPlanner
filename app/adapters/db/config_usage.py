"""What the installation knows about its groups that a document cannot say (ADR 0099 point 7).

The planner refuses to delete a group something refers to, to change the time grid of a group
with future bookings, and to lower a capacity under what is booked. It cannot see bookings, so
``read_usage`` reads them: which groups are referenced (as the group eraser decides), which
hold a future booking, and the places taken on every future slot. Counts and times only; no
participant data is read.
"""

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.config_flows import read_usage_of_flows
from app.adapters.db.legacy_slot import legacy_slot
from app.adapters.db.models import (
    ApiClientGroupRow,
    BookingRow,
    ClosureRow,
    GroupRow,
    IntegrationReportRow,
    OrganisationRow,
    ShortfallAcceptanceRow,
    SlotRow,
)
from app.domain.booking import HOLDS_PLACE
from app.domain.configuration_changes import SlotLoad, Usage
from app.domain.configuration_v1_names import GROUP_KIND
from app.domain.guest_access import guest_check_passed
from app.domain.integration_check import SERVICE_APP_REPORT

_HOLDS_PLACE = sorted(status.value for status in HOLDS_PLACE)

type Address = tuple[str, str]


async def _addresses(session: AsyncSession, *conditions: object) -> set[Address]:
    """Return the (organisation, group) slugs of the groups that meet every condition."""
    query = (
        select(OrganisationRow.slug, GroupRow.slug)
        .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
        .where(*conditions)  # type: ignore[arg-type]
    )
    return {(organisation, group) for organisation, group in (await session.execute(query)).all()}


async def read_usage(
    session: AsyncSession, now: datetime, locked_settings: frozenset[str]
) -> Usage:
    """Return the facts about groups the planner checks a document against.

    A group is in use when a booking, a closure, a shortfall acceptance or an API client's
    grant refers to it: the four references the group eraser counts, since their tables are
    ``ON DELETE RESTRICT`` or kept on purpose. Slots do not count, as for the eraser. No other
    kind has a database reference that blocks its deletion beyond its groups.

    Args:
        session: The open transaction.
        now: The current time (UTC); a slot or booking counts when it starts after it.
        locked_settings: The settings the environment locks, passed through to the planner.
    """
    in_use: set[Address] = set()
    for reference in (
        select(BookingRow.id).where(BookingRow.group_id == GroupRow.id),
        select(ClosureRow.id).where(ClosureRow.group_id == GroupRow.id),
        select(ShortfallAcceptanceRow.group_id).where(
            ShortfallAcceptanceRow.group_id == GroupRow.id
        ),
        select(ApiClientGroupRow.group_id).where(ApiClientGroupRow.group_id == GroupRow.id),
    ):
        in_use |= await _addresses(session, reference.exists())
    future = await _addresses(
        session,
        select(BookingRow.id)
        .join(SlotRow, SlotRow.id == BookingRow.slot_id)
        .where(
            BookingRow.group_id == GroupRow.id,
            BookingRow.status.in_(_HOLDS_PLACE),
            SlotRow.start > now,
        )
        .exists(),
    )
    loads: dict[Address, list[SlotLoad]] = {}
    slots = await session.execute(
        select(
            OrganisationRow.slug,
            GroupRow.slug,
            SlotRow.start,
            SlotRow.end,
            func.count(BookingRow.id),
        )
        .select_from(SlotRow)
        .join(GroupRow, GroupRow.id == SlotRow.group_id)
        .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
        .outerjoin(
            BookingRow,
            (BookingRow.slot_id == SlotRow.id) & BookingRow.status.in_(_HOLDS_PLACE),
        )
        .where(SlotRow.start > now, legacy_slot())
        .group_by(SlotRow.id, OrganisationRow.slug, GroupRow.slug)
        .order_by(SlotRow.start)
    )
    for organisation, group, start, end, booked in slots.all():
        loads.setdefault((organisation, group), []).append(SlotLoad(start, end, int(booked)))
    return Usage(
        in_use={GROUP_KIND: frozenset(in_use)},
        kept=await read_usage_of_flows(session),
        future_bookings=frozenset(future),
        slot_loads={address: tuple(found) for address, found in loads.items()},
        locked_settings=locked_settings,
        guest_check_passed=await _guest_check_passed(session),
    )


async def _guest_check_passed(session: AsyncSession) -> bool:
    """Return whether the stored service-app check minted a guest token (ADR 0123 point 17)."""
    row = await session.get(IntegrationReportRow, SERVICE_APP_REPORT)
    if row is None:
        return False
    try:
        body = json.loads(row.body)
    except ValueError:
        return False
    return guest_check_passed(body if isinstance(body, dict) else None)
