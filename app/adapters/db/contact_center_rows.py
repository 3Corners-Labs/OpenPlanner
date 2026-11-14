"""Storing a group's contact-center routing and a booking's task (ADR 0032, ADR 0116).

The expand step of ADR 0116 §5: the previous minor's image must keep working on this schema,
during a rolling update and after a restore. So for Webex Contact Center, the only platform the
previous image knows, the old columns stay the source of truth: every write fills them and every
read takes them. ``group_contact_center`` and ``bookings.contact_center_task_id`` /
``contact_center_kind`` are written alongside, and read only for another platform (none yet).
An edit or a purge the previous image makes in the old columns is therefore never undone by a
stale copy in the new places.

    write ─► groups.wxcc_*          + group_contact_center   (webex_cc: old columns win on read)
          ─► bookings.wxcc_task_id  + contact_center_task_id/kind
    read  ─► kind None or webex_cc ─► the old columns
          ─► another kind          ─► the new place

This module is the one home of that rule; ``task_of``, ``stored_task_id`` and ``purge_columns``
apply it to rows and to SQL.

The task kind (ADR 0123 step 7a) is newer than the previous image, which never reads or writes
it, so it has no old column to keep in step: ``group_contact_center.task_kind`` holds a routing's
kind for every platform (the form where the group has no such row) and
``bookings.contact_center_task_kind`` the kind a task was created with (NULL: a form, as every
task before the step) and ``bookings.contact_center_attempt_kind`` the kind the latest create was
tried as (NULL: a form; cleared with a stored task or a failed attempt).
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, and_, case, delete, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.contact_centers import attributes_from_json, attributes_json, routing
from app.adapters.db.models import BookingRow, GroupContactCenterRow, GroupRow
from app.adapters.wxcc import BUSINESS_ADDRESS, CHANNEL
from app.domain.contact_center import (
    ContactCenterKind,
    ContactCenterRouting,
    ContactCenterTaskRef,
    TaskKind,
)
from app.domain.privacy import PURGED_FIELDS

LEGACY_KIND = ContactCenterKind.WEBEX_CC
"""The platform of the old columns; for it they are the source of truth in this minor."""
TASK_COLUMNS = frozenset({"contact_center_task_id", "wxcc_task_id"})
"""The two columns that hold a booking's task id; ``stored_task_id`` reads them as one."""


def routing_of(
    group: GroupRow, center: GroupContactCenterRow | None
) -> ContactCenterRouting | None:
    """Return the group's routing: the old columns for WxCC, ``group_contact_center`` otherwise."""
    if center is not None and ContactCenterKind(center.kind) is not LEGACY_KIND:
        return routing(
            ContactCenterKind(center.kind),
            center.routing_value,
            attributes_from_json(center.attributes),
            TaskKind(center.task_kind),
        )
    return routing(
        LEGACY_KIND,
        group.wxcc_routing_value,
        {BUSINESS_ADDRESS: group.wxcc_business_address, CHANNEL: group.wxcc_channel},
        TaskKind(center.task_kind) if center is not None else TaskKind.FORM,
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
            task_kind=center.task_kind.value,
        )
    )


def task_of(row: BookingRow) -> ContactCenterTaskRef | None:
    """Return the booking's task: ``wxcc_task_id`` for WxCC (or no kind), the new column else."""
    kind = ContactCenterKind(row.contact_center_kind) if row.contact_center_kind else LEGACY_KIND
    task_id = row.wxcc_task_id if kind is LEGACY_KIND else row.contact_center_task_id
    if task_id is None:
        return None
    task_kind = TaskKind(row.contact_center_task_kind or TaskKind.FORM)
    return ContactCenterTaskRef(task_id, kind, task_kind)


def task_columns(task: ContactCenterTaskRef) -> dict[str, Any]:
    """Return the columns that store ``task``, the old column included for WxCC."""
    return {
        "contact_center_task_id": task.id,
        "contact_center_kind": task.kind.value,
        "wxcc_task_id": task.id if task.kind is LEGACY_KIND else None,
        "contact_center_task_kind": task.task_kind.value,
        "contact_center_attempt_kind": None,  # the create is answered: nothing left to adopt
    }


def _legacy_kind() -> ColumnElement[bool]:
    """Return the condition for a row whose task, if any, is WxCC's (no kind, or ``webex_cc``)."""
    return or_(
        BookingRow.contact_center_kind.is_(None),
        BookingRow.contact_center_kind == LEGACY_KIND.value,
    )


def stored_task_id() -> ColumnElement[str | None]:
    """Return the task id as one column expression, by the rule of ``task_of``."""
    return case((_legacy_kind(), BookingRow.wxcc_task_id), else_=BookingRow.contact_center_task_id)


def stored_task_kind() -> ColumnElement[str]:
    """Return the task's platform as one column expression; ``webex_cc`` for an old row."""
    return func.coalesce(BookingRow.contact_center_kind, LEGACY_KIND.value)


def has_any_task() -> ColumnElement[bool]:
    """Return the condition for a booking with a task."""
    return stored_task_id().is_not(None)


def without_task() -> ColumnElement[bool]:
    """Return the condition for a booking without a task."""
    return stored_task_id().is_(None)


def has_task(task_id: str, kind: ContactCenterKind) -> ColumnElement[bool]:
    """Return the condition for the booking whose task is ``task_id`` at ``kind``.

    Each branch is indexed: Webex Contact Center reads the old column (``task_of``), another
    platform the new column together with its kind, so one platform's id never finds a
    booking whose task is at another (ADR 0032 point 6).
    """
    if kind is LEGACY_KIND:
        return and_(BookingRow.wxcc_task_id == task_id, _legacy_kind())
    return and_(
        BookingRow.contact_center_task_id == task_id,
        BookingRow.contact_center_kind == kind.value,
    )


def purge_columns() -> Sequence[ColumnElement[Any]]:
    """Return what the purge empties, as read: ``PURGED_FIELDS`` with the task read as one.

    A purge by the previous image empties ``wxcc_task_id`` and leaves the WxCC copy in
    ``contact_center_task_id``; by the rule of ``task_of`` the booking holds no task then.
    """
    columns: list[ColumnElement[Any]] = [
        getattr(BookingRow, name) for name in PURGED_FIELDS if name not in TASK_COLUMNS
    ]
    return [*columns, stored_task_id()]
