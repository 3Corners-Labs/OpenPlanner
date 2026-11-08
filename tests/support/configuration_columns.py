"""Which columns of the configuration tables a format's kinds leave uncovered (ADR 0099 point 3).

A document must reach every configuration table (ADR 0089 point 2), and a new column in one of
them must not slip through unmapped. A column is covered when a field of a kind that covers
the table names it (a field's own name, or ``columns``: a bare name applies to every table of
the kind, ``table.column`` to that table only), when the kind lists it as ``derived``, or when
it is a surrogate ``id`` or a foreign key (the tree itself says where an object belongs).
"""

from collections import defaultdict
from collections.abc import Iterable

from app.adapters.db.models import Base
from app.domain.configuration_schema import Kind


def uncovered_columns(kinds: Iterable[Kind]) -> dict[str, set[str]]:
    """Return, per table the kinds cover, the columns no field and no ``derived`` entry names."""
    covered: dict[str, set[str]] = defaultdict(set)
    tables: set[str] = set()
    for kind in kinds:
        tables |= kind.tables
        for table in kind.tables:
            for spec in kind.fields:
                for column in spec.columns or (spec.name,):
                    owner, _, name = column.rpartition(".")
                    if owner in ("", table):
                        covered[table].add(name)
            covered[table].update(
                entry.split(".", 1)[1] for entry in kind.derived if entry.startswith(f"{table}.")
            )
    missing: dict[str, set[str]] = {}
    for name in sorted(tables):
        table = Base.metadata.tables[name]
        exempt = {c.name for c in table.columns if c.foreign_keys or c.name == "id"}
        left = {c.name for c in table.columns} - covered[name] - exempt
        if left:
            missing[name] = left
    return missing
