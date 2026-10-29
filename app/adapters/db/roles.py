"""SQL queries of custom roles (ADR 0039).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. Permissions are stored space separated; a stored value that is no longer a
permission or template is dropped when read, so it can only ever grant less.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import RoleRow
from app.domain.authorization import CustomRole, Permission, Role

_PERMISSIONS = frozenset(p.value for p in Permission)


def _role(row: RoleRow) -> CustomRole | None:
    """Map a row; ``None`` for a template that no longer exists (grants nothing)."""
    if row.template not in {r.value for r in Role}:
        return None
    return CustomRole(
        id=row.id,
        template=Role(row.template),
        permissions=frozenset(Permission(p) for p in row.permissions.split() if p in _PERMISSIONS),
        claim_value=row.claim_value,
        name_nl=row.name_nl,
        name_en=row.name_en,
        version=row.version,
    )


def _fill(row: RoleRow, role: CustomRole) -> None:
    """Copy the changeable fields of ``role`` onto ``row``."""
    row.name_nl = role.name_nl
    row.name_en = role.name_en
    row.claim_value = role.claim_value
    row.permissions = " ".join(sorted(role.permissions))
    row.version = role.version


class RoleQueries:
    """The custom roles' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def get_custom_roles(self) -> list[CustomRole]:
        """Return every custom role, ordered by id."""
        rows = (await self._session.scalars(select(RoleRow).order_by(RoleRow.id))).all()
        return [role for role in map(_role, rows) if role is not None]

    async def get_custom_role(self, role_id: int) -> CustomRole | None:
        """Return the custom role with ``role_id``."""
        row = await self._session.get(RoleRow, role_id)
        return _role(row) if row else None

    async def add_custom_role(self, role: CustomRole) -> CustomRole:
        """Store a new custom role and return it with its id."""
        row = RoleRow(template=role.template.value)
        _fill(row, role)
        self._session.add(row)
        await self._session.flush()
        stored = _role(row)
        if stored is None:  # pragma: no cover - the template was just written from a Role
            raise LookupError("unknown template")
        return stored

    async def save_custom_role(self, role: CustomRole) -> None:
        """Store names, claim value, permissions and version of an existing role."""
        row = await self._session.get(RoleRow, role.id)
        if row is None:
            raise LookupError("unknown role")
        _fill(row, role)
        await self._session.flush()

    async def delete_custom_role(self, role_id: int) -> None:
        """Delete a custom role."""
        await self._session.execute(delete(RoleRow).where(RoleRow.id == role_id))
