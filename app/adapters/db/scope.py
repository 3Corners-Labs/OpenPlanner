"""The one translation of a ``Scope`` into SQL (ADR 0014), shared by every listing query."""

from typing import Any

from sqlalchemy import ColumnElement, false, or_, true
from sqlalchemy.sql.elements import SQLCoreOperations

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
        conditions.append(group_slug.in_(sorted(scope.groups)))
    return or_(*conditions) if conditions else false()
