"""The one SQL predicate that keeps a pool slot out of a service's own slot queries (ADR 0122).

A slot of a non-legacy flow carries the counted pool it draws on (``slots.capacity_pool_id``).
Every query that treats a service's scheduled slots as the service's own staffing, capacity or
reporting selects only the *legacy* ones, through ``legacy_slot()``, so that a pool booking at
the same service and start is never counted in (or found as) a legacy slot. A query that forgets
it would quietly sell a person twice; ``tests/adapters/db/test_legacy_slot.py`` fails when a
query that selects scheduled slots does not use it.
"""

from sqlalchemy import ColumnElement

from app.adapters.db.models import SlotRow


def legacy_slot() -> ColumnElement[bool]:
    """Return the condition that a slot belongs to a service's own staffing (no pool)."""
    return SlotRow.capacity_pool_id.is_(None)
