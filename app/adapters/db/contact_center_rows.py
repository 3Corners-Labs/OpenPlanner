"""Storing a group's contact-center routing and a booking's task (ADR 0032, ADR 0116).

The expand step of ADR 0116: ``group_contact_center`` and ``bookings.contact_center_task_id``
/ ``contact_center_kind`` are the new places. Until the old columns are dropped a minor later,
every write also fills them (for a Webex Contact Center routing or task), and a read takes the
new place first and the old columns when the new one is empty, so a row an older version wrote
still reads and a downgrade loses nothing.

    write ─► group_contact_center  + groups.wxcc_*        (webex_cc only)
          ─► bookings.contact_center_task_id/kind + wxcc_task_id
    read  ─► new place ─► else the old columns (kind webex_cc)
"""

from typing import Any

from sqlalchemy import ColumnElement, delete, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.contact_centers import attributes_from_json, attributes_json, routing
from app.adapters.db.models import BookingRow, GroupContactCenterRow, GroupRow
from app.adapters.wxcc import BUSINESS_ADDRESS, CHANNEL
from app.domain.contact_center import ContactCenterKind, ContactCenterRouting

LEGACY_KIND = ContactCenterKind.WEBEX_CC
"""The platform of a routing or task stored before ADR 0032 (the old columns are WxCC's)."""


def routing_of(
    group: GroupRow, center: GroupContactCenterRow | None
) -> ContactCenterRouting | None:
    """Return the group's routing: from ``group_contact_center``, else the old columns."""
    if center is not None:
        return routing(
            ContactCenterKind(center.kind),
            center.routing_value,
            attributes_from_json(center.attributes),
        )
    return routing(
        LEGACY_KIND,
        group.wxcc_routing_value,
        {BUSINESS_ADDRESS: group.wxcc_business_address, CHANNEL: group.wxcc_channel},
    )


def legacy_group_columns(center: ContactCenterRouting | None) -> dict[str, Any]:
    """Return the old ``groups.wxcc_*`` columns for ``center``; empty for another platform."""
    legacy = center if center is not None and center.kind is LEGACY_KIND else None
    return {
        "wxcc_business_address": legacy.attribute(BUSINESS_ADDRESS) if legacy else None,
        "wxcc_channel": legacy.attribute(CHANNEL) if legacy else None,
        "wxcc_routing_value": legacy.routing_value if legacy else None,
    }


async def save_routing(
    session: AsyncSession, group_id: int, center: ContactCenterRouting | None
) -> None:
    """Replace the group's ``group_contact_center`` row with ``center``, or remove it."""
    if center is None:
        await session.execute(
            delete(GroupContactCenterRow).where(GroupContactCenterRow.group_id == group_id)
        )
        return
    await session.merge(
        GroupContactCenterRow(
            group_id=group_id,
            kind=center.kind.value,
            routing_value=center.routing_value,
            attributes=attributes_json(center.attributes),
        )
    )


def task_of(row: BookingRow) -> tuple[str | None, ContactCenterKind | None]:
    """Return the booking's task id and platform: the new columns, else ``wxcc_task_id``."""
    if row.contact_center_task_id is not None:
        kind = row.contact_center_kind
        return row.contact_center_task_id, ContactCenterKind(kind) if kind else LEGACY_KIND
    if row.wxcc_task_id is not None:
        return row.wxcc_task_id, LEGACY_KIND
    return None, None


def task_columns(task_id: str, kind: ContactCenterKind) -> dict[str, Any]:
    """Return the columns that store task ``task_id`` of ``kind``, old column included."""
    return {
        "contact_center_task_id": task_id,
        "contact_center_kind": kind.value,
        "wxcc_task_id": task_id if kind is LEGACY_KIND else None,
    }


def has_any_task() -> ColumnElement[bool]:
    """Return the condition for a booking with a task in either column."""
    return ~without_task()


def has_task(task_id: str) -> ColumnElement[bool]:
    """Return the condition for the booking whose task is ``task_id``, in either column."""
    return or_(BookingRow.contact_center_task_id == task_id, BookingRow.wxcc_task_id == task_id)


def without_task() -> ColumnElement[bool]:
    """Return the condition for a booking with no task in either column."""
    return BookingRow.contact_center_task_id.is_(None) & BookingRow.wxcc_task_id.is_(None)


def stored_task_id() -> ColumnElement[str | None]:
    """Return the task id as one column expression: the new column, else the old one."""
    return func.coalesce(BookingRow.contact_center_task_id, BookingRow.wxcc_task_id)


def stored_task_kind() -> ColumnElement[str]:
    """Return the task's platform as one column expression; ``webex_cc`` for an old row."""
    return func.coalesce(BookingRow.contact_center_kind, LEGACY_KIND.value)
