"""SQL queries of the staff directory (ADR 0046, ``staff.view``).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. Who may see whom is ``app.domain.staff.visible``; ``_visible`` is the same rule in
SQL: a superuser record only for the superuser's scope, anyone else when one of their
memberships is inside the scope (through ``scope_filter``, the one translation of a scope).
"""

from datetime import datetime
from typing import Protocol

from sqlalchemy import ColumnElement, and_, delete, exists, func, literal, not_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import StaffMembershipRow, StaffRow
from app.adapters.db.scope import scope_filter
from app.domain.authorization import Role, Scope
from app.domain.staff import Membership, StaffFilter, StaffMember


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession


def _has_role(role: Role) -> ColumnElement[bool]:
    """Whether the comma-separated ``roles`` column holds ``role``."""
    padded = literal(",") + func.coalesce(StaffRow.roles, "") + literal(",")
    return padded.contains(f",{role.value},")


def _has_membership(*conditions: ColumnElement[bool]) -> ColumnElement[bool]:
    """Whether the staff row has a membership meeting every condition."""
    return exists().where(StaffMembershipRow.staff_id == StaffRow.id, *conditions)


def _visible(scope: Scope) -> ColumnElement[bool]:
    """The SQL side of ``visible``: who a viewer with ``scope`` may see."""
    if scope.everything:
        return true()
    in_scope = scope_filter(scope, StaffMembershipRow.organisation, StaffMembershipRow.group)
    return and_(not_(_has_role(Role.SUPERUSER)), _has_membership(in_scope))


def _narrowed(narrow: StaffFilter) -> list[ColumnElement[bool]]:
    """The SQL side of ``StaffFilter.matches``."""
    conditions: list[ColumnElement[bool]] = []
    if narrow.organisation is not None:
        conditions.append(_has_membership(StaffMembershipRow.organisation == narrow.organisation))
    if narrow.group is not None:
        conditions.append(_has_membership(StaffMembershipRow.group == narrow.group))
    if narrow.role is not None:
        conditions.append(_has_role(narrow.role))
    if narrow.dormant_before is not None:
        conditions.append(StaffRow.last_login_at < narrow.dormant_before)
    if narrow.id is not None:
        conditions.append(StaffRow.id == narrow.id)
    return conditions


def _member(row: StaffRow, memberships: list[StaffMembershipRow]) -> StaffMember:
    """Map a staff row and its membership rows."""
    roles = frozenset(Role(r) for r in (row.roles or "").split(",") if r in set(Role))
    return StaffMember(
        subject=row.subject,
        display_name=row.display_name,
        roles=roles,
        memberships=frozenset(Membership(m.organisation, m.group) for m in memberships),
        first_seen_at=row.first_seen_at,
        last_login_at=row.last_login_at,
        last_seen_at=row.last_seen_at,
        last_logout_at=row.last_logout_at,
        recorded=row.roles is not None,
        id=row.id,
    )


class StaffQueries:
    """The staff directory's part of ``SqlTransaction``."""

    async def get_staff(self: _Transaction, subject: str) -> StaffMember | None:
        """Return the staff record of ``subject`` with all its memberships."""
        row = await self._session.scalar(select(StaffRow).where(StaffRow.subject == subject))
        if row is None:
            return None
        memberships = await self._session.scalars(
            select(StaffMembershipRow).where(StaffMembershipRow.staff_id == row.id)
        )
        return _member(row, list(memberships))

    async def save_staff(self: _Transaction, member: StaffMember) -> None:
        """Create or replace the staff record of ``member.subject`` and its memberships."""
        row = await self._session.scalar(select(StaffRow).where(StaffRow.subject == member.subject))
        if row is None:
            row = StaffRow(subject=member.subject[:255])
            self._session.add(row)
        row.display_name = member.display_name[:255]
        row.roles = ",".join(sorted(member.roles))
        row.first_seen_at = member.first_seen_at
        row.last_login_at = member.last_login_at
        row.last_seen_at = member.last_seen_at
        row.last_logout_at = member.last_logout_at
        await self._session.flush()
        await self._session.execute(
            delete(StaffMembershipRow).where(StaffMembershipRow.staff_id == row.id)
        )
        self._session.add_all(
            StaffMembershipRow(staff_id=row.id, organisation=m.organisation, group=m.group)
            for m in sorted(member.memberships, key=lambda m: m.sort_key)
        )
        await self._session.flush()

    async def set_staff_seen(self: _Transaction, subject: str, at: datetime) -> None:
        """Record the latest request of ``subject``; unknown subjects are ignored."""
        row = await self._session.scalar(select(StaffRow).where(StaffRow.subject == subject))
        if row is not None:
            row.last_seen_at = at
            await self._session.flush()

    async def set_staff_logout(self: _Transaction, subject: str, at: datetime) -> None:
        """Record the latest logout of ``subject``; unknown subjects are ignored."""
        row = await self._session.scalar(select(StaffRow).where(StaffRow.subject == subject))
        if row is not None:
            row.last_logout_at = at
            await self._session.flush()

    async def list_staff(
        self: _Transaction, scope: Scope, narrow: StaffFilter
    ) -> list[StaffMember]:
        """Return the staff ``scope`` may see, narrowed by ``narrow``, by name."""
        rows = list(
            await self._session.scalars(
                select(StaffRow)
                .where(_visible(scope), *_narrowed(narrow))
                .order_by(StaffRow.display_name, StaffRow.subject)
            )
        )
        if not rows:
            return []
        memberships: dict[int, list[StaffMembershipRow]] = {row.id: [] for row in rows}
        for membership in await self._session.scalars(
            select(StaffMembershipRow).where(StaffMembershipRow.staff_id.in_(list(memberships)))
        ):
            memberships[membership.staff_id].append(membership)
        return [_member(row, memberships[row.id]) for row in rows]
