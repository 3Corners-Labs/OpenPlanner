"""The one translation of an authorisation ``Scope`` into SQL (ADR 0014).

Every scoped listing of the database adapter (``uow`` and its query mixins) filters through
``scope_filter``, so what a list shows is exactly what ``decide`` allows.
"""

from typing import Any

from sqlalchemy import ColumnElement, and_, false, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import SQLCoreOperations

from app.adapters.db.models import OrganisationRow
from app.domain.authorization import Scope


def scope_filter(
    scope: Scope,
    organisation_slug: SQLCoreOperations[Any],
    group_slug: SQLCoreOperations[Any] | None,
) -> ColumnElement[bool]:
    """Translate a ``Scope`` into a SQL condition; the one place this happens (ADR 0014).

    An empty scope matches nothing; ``everything`` matches all rows.
    """
    if scope.everything:
        return true()
    conditions: list[ColumnElement[bool]] = []
    if scope.organisations:
        conditions.append(organisation_slug.in_(sorted(scope.organisations)))
    if scope.groups and group_slug is not None:
        # ADR 0049 point 11: a group only together with its organisation.
        conditions += [
            and_(organisation_slug == organisation, group_slug == group)
            for organisation, group in sorted(scope.groups)
        ]
    return or_(*conditions) if conditions else false()


def visible_organisation(scope: Scope, slug: Any) -> ColumnElement[bool]:
    """Return the condition that an organisation (by slug column) is visible in ``scope``.

    The organisation itself or any group of it is enough: reading the catalogue of skills and
    capacity pools is what a service admin needs to refer to them (ADR 0121 decision D2). Who may
    *change* them is the permission of the route, never decided here.
    """
    if scope.everything:
        return true()
    slugs = sorted(scope.organisations | {organisation for organisation, _ in scope.groups})
    return slug.in_(slugs) if slugs else false()


async def organisation_is_visible(
    session: AsyncSession, scope: Scope, organisation_id: int
) -> bool:
    """Return whether the organisation exists and ``visible_organisation`` holds for ``scope``."""
    found = await session.scalar(
        select(OrganisationRow.id).where(
            OrganisationRow.id == organisation_id, visible_organisation(scope, OrganisationRow.slug)
        )
    )
    return found is not None
