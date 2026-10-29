"""CSV export of the audit log: exactly the current filter, inside the admin's scope (ADR 0040).

The file is meant for Dutch Excel as much as for scripts: UTF-8 with a byte order mark, ``;``
as separator, RFC 4180 quoting and CRLF line ends. The columns are the fields of
``openplanner.event/1`` in schema order, so the export, the log platform and the table speak
one language. A cell that a spreadsheet would read as a formula (starting with ``=``, ``+``,
``-``, ``@``, tab or carriage return) gets a leading ``'`` (CSV injection, OWASP).

    AuditFilter ─► export_window() check ─► rows (≤ max) ─► to_csv() ─► file
                                                         └► audit event ``audit.export``

Limits keep one export from holding the database or the admin's browser: at most
``AUDIT_EXPORT_MAX_ROWS`` rows and ``AUDIT_EXPORT_MAX_DAYS`` days; an export over a limit is
refused with a code, never cut short silently.
"""

import csv
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.event_schema import FIELDS, Value

EXPORT_EVENT = "audit.export"
"""Audit event of every export, with ``rows:<n>`` as detail."""

BOM = "﻿"
"""Byte order mark: tells Excel the file is UTF-8."""

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class ExportRefused(Exception):  # noqa: N818 - a refusal with a code, like DomainError
    """The export is over a limit; ``code`` says which (``too_many_days``, ``too_many_rows``)."""

    def __init__(self, code: str) -> None:
        """Store the code."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExportLimits:
    """How large one export may be.

    Attributes:
        max_rows: Most rows in one file.
        max_days: Longest period one file may cover.
    """

    max_rows: int = 100_000
    max_days: int = 366


def export_window(
    since: datetime | None, until: datetime | None, now: datetime, limits: ExportLimits
) -> tuple[datetime, datetime]:
    """Return the period to export: the filter's, or the last ``max_days`` up to now.

    Raises:
        ExportRefused: ``too_many_days`` when the filter's period is longer than allowed.
    """
    end = until or now
    start = since or end - timedelta(days=limits.max_days)
    if end - start > timedelta(days=limits.max_days):
        raise ExportRefused("too_many_days")
    return start, end


def safe_cell(value: Value) -> str:
    """Return ``value`` as a cell a spreadsheet will not run as a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def to_csv(records: Iterable[Mapping[str, Value]]) -> str:
    """Write ``records`` as the export file: BOM, header, one row per record."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(FIELDS)
    for record in records:
        writer.writerow([safe_cell(record[name]) for name in FIELDS])
    return BOM + out.getvalue()
