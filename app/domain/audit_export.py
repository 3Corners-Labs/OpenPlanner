"""CSV export of the audit log: exactly the current filter, inside the admin's scope (ADR 0040).

The columns are the fields of ``openplanner.event/2`` in schema order, so the export, the log
platform and the table speak one language. The writer, its escaping and its limits are the
shared ones of ``app.domain.csv_export`` (ADR 0117 step 8).

    AuditFilter ─► export_window() check ─► rows (≤ max) ─► audit_csv() ─► file
                                                         └► audit event ``audit.export``
"""

from collections.abc import Iterable, Mapping

from app.domain.csv_export import Cell, to_csv
from app.domain.event_schema import FIELDS

EXPORT_EVENT = "audit.export"
"""Audit event of every export, with ``rows:<n>`` as detail."""


def audit_csv(records: Iterable[Mapping[str, Cell]]) -> str:
    """Write ``records`` as the audit export: BOM, the schema's header, one row per record."""
    return to_csv(FIELDS, ([record[name] for name in FIELDS] for record in records))
