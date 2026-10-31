"""SQL queries of the staff directory (ADR 0046, ``staff.view``) and the directory (ADR 0049).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. Who may see whom is ``app.domain.staff.visible``; ``_visible`` is the same rule in
SQL: a superuser record only for the superuser's scope, anyone else when one of their
memberships is inside the scope (through ``scope_filter``, the one translation of a scope).

``get_directory`` loads what a claim value can name (role keys, organisations and groups with
their IdP values) once per request; ``list_grant_holders`` and ``list_refused_entitlements``
feed the identity provider mapping page.
"""

from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import (
    ColumnElement,
    and_,
    delete,
    exists,
    func,
    literal,
    not_,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import SQLCoreOperations

from app.adapters.db.models import (
    GroupRow,
    OrganisationRow,
    StaffMembershipRow,
    StaffRefusalRow,
    StaffRow,
)
from app.adapters.db.scope import scope_filter
from app.domain.authorization import CustomRole, Role, RoleCatalogue, Scope
from app.domain.entitlements import Directory, GroupRef, OrganisationRef
from app.domain.staff import (
    GrantHolders,
    Membership,
    RefusedEntitlement,
    StaffFilter,
    StaffMember,
)


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction`` (its session and the role queries)."""

    _session: AsyncSession

    async def get_custom_roles(self) -> list[CustomRole]:
        """Return every custom role (``RoleQueries``)."""
        ...


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
    if narrow.unrecognised:
        conditions.append(exists().where(StaffRefusalRow.staff_id == StaffRow.id))
    return conditions


def _organisation_in(scope: Scope, column: SQLCoreOperations[Any]) -> ColumnElement[bool]:
    """Whether an organisation column (slug) falls in ``scope``'s organisations."""
    if scope.everything:
        return true()
    return column.in_(sorted(scope.organisations))


def _member(
    row: StaffRow, memberships: list[StaffMembershipRow], refusals: list[StaffRefusalRow]
) -> StaffMember:
    """Map a staff row with its membership and refusal rows."""
    roles = frozenset(Role(r) for r in (row.roles or "").split(",") if r in set(Role))
    return StaffMember(
        subject=row.subject,
        display_name=row.display_name,
        roles=roles,
        memberships=frozenset(Membership(m.role_key, m.organisation, m.group) for m in memberships),
        first_seen_at=row.first_seen_at,
        last_login_at=row.last_login_at,
        last_seen_at=row.last_seen_at,
        last_logout_at=row.last_logout_at,
        recorded=row.roles is not None,
        id=row.id,
        refused=tuple(
            RefusedEntitlement(r.value, r.reason, r.organisation, r.received_at)
            for r in sorted(refusals, key=lambda r: r.value)
        ),
    )


class StaffQueries:
    """The staff directory's part of ``SqlTransaction``."""

    async def get_staff(self: _Transaction, subject: str) -> StaffMember | None:
        """Return the staff record of ``subject`` with all its memberships and refusals."""
        row = await self._session.scalar(select(StaffRow).where(StaffRow.subject == subject))
        if row is None:
            return None
        memberships = await self._session.scalars(
            select(StaffMembershipRow).where(StaffMembershipRow.staff_id == row.id)
        )
        refusals = await self._session.scalars(
            select(StaffRefusalRow).where(StaffRefusalRow.staff_id == row.id)
        )
        return _member(row, list(memberships), list(refusals))

    async def save_staff(self: _Transaction, member: StaffMember) -> None:
        """Create or replace the staff record of ``member.subject`` with its memberships."""
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
        await self._session.execute(
            delete(StaffRefusalRow).where(StaffRefusalRow.staff_id == row.id)
        )
        self._session.add_all(
            StaffMembershipRow(
                staff_id=row.id, role_key=m.role[:40], organisation=m.organisation, group=m.group
            )
            for m in sorted(member.memberships, key=lambda m: m.sort_key)
        )
        self._session.add_all(
            StaffRefusalRow(
                staff_id=row.id,
                value=r.value[:200],
                reason=r.reason[:40],
                organisation=r.organisation,
                received_at=r.received_at,
            )
            for r in member.refused
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
        ids = [row.id for row in rows]
        memberships: dict[int, list[StaffMembershipRow]] = {row.id: [] for row in rows}
        for membership in await self._session.scalars(
            select(StaffMembershipRow).where(StaffMembershipRow.staff_id.in_(ids))
        ):
            memberships[membership.staff_id].append(membership)
        refusals: dict[int, list[StaffRefusalRow]] = {row.id: [] for row in rows}
        for refusal in await self._session.scalars(
            select(StaffRefusalRow).where(StaffRefusalRow.staff_id.in_(ids))
        ):
            refusals[refusal.staff_id].append(refusal)
        return [_member(row, memberships[row.id], refusals[row.id]) for row in rows]

    async def get_directory(self: _Transaction) -> Directory:
        """Return the role keys, organisations and groups a claim value can name (ADR 0049)."""
        organisations = [
            OrganisationRef(slug, idp_key or slug)
            for slug, idp_key in await self._session.execute(
                select(OrganisationRow.slug, OrganisationRow.idp_key).order_by(OrganisationRow.slug)
            )
        ]
        groups = [
            GroupRef(slug, idp_key or slug, organisation)
            for slug, idp_key, organisation in await self._session.execute(
                select(GroupRow.slug, GroupRow.idp_key, OrganisationRow.slug)
                .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
                .order_by(GroupRow.slug)
            )
        ]
        catalogue = RoleCatalogue(tuple(await self.get_custom_roles()))
        return Directory.of(catalogue, organisations, groups)

    async def list_grant_holders(self: _Transaction, scope: Scope) -> list[GrantHolders]:
        """Return, per grant on an organisation in ``scope``, how many people last held it."""
        rows = await self._session.execute(
            select(
                StaffMembershipRow.role_key,
                StaffMembershipRow.organisation,
                StaffMembershipRow.group,
                func.count(func.distinct(StaffMembershipRow.staff_id)),
                func.max(StaffRow.last_login_at),
            )
            .join(StaffRow, StaffRow.id == StaffMembershipRow.staff_id)
            .where(_organisation_in(scope, StaffMembershipRow.organisation))
            .group_by(
                StaffMembershipRow.role_key,
                StaffMembershipRow.organisation,
                StaffMembershipRow.group,
            )
            .order_by(StaffMembershipRow.organisation, StaffMembershipRow.group)
        )
        return [
            GrantHolders(Membership(role, organisation, group), int(count), last_login)
            for role, organisation, group, count, last_login in rows
        ]

    async def list_refused_entitlements(
        self: _Transaction, scope: Scope, since: datetime
    ) -> list[RefusedEntitlement]:
        """Return the refused values received since ``since`` in ``scope``, newest first."""
        rows = await self._session.scalars(
            select(StaffRefusalRow)
            .where(
                StaffRefusalRow.received_at >= since,
                _organisation_in(scope, StaffRefusalRow.organisation),
            )
            .order_by(StaffRefusalRow.received_at.desc(), StaffRefusalRow.value)
        )
        return [RefusedEntitlement(r.value, r.reason, r.organisation, r.received_at) for r in rows]
