"""One CSV writer for every export: the audit log and the usage reports (ADR 0040, ADR 0117).

The file is meant for Dutch Excel as much as for scripts: UTF-8 with a byte order mark, ``;``
as separator, RFC 4180 quoting and CRLF line ends. A cell that a spreadsheet would read as a
formula (starting with ``=``, ``+``, ``-``, ``@``, tab or carriage return) gets a leading ``'``
(CSV injection, OWASP). Every export shares this one implementation and this one escaping.

    rows ─► limits check (``export_window``, row count) ─► ``to_csv`` ─► file ─► ``checksum``

Limits keep one export from holding the database or the admin's browser: an export over a limit
is refused with a code, never cut short silently. The file ends with no checksum line; the
response carries the SHA-256 of the file as ``X-Content-SHA256`` and the audit event stores it.
"""

import csv
import hashlib
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

type Cell = str | int | float | bool | None
"""What one cell may hold before it is written as text."""

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


def check_span(span: timedelta, limits: ExportLimits) -> None:
    """Refuse a period longer than ``limits.max_days`` days: the one home of that rule.

    Raises:
        ExportRefused: ``too_many_days``.
    """
    if span > timedelta(days=limits.max_days):
        raise ExportRefused("too_many_days")


def export_window(
    since: datetime | None, until: datetime | None, now: datetime, limits: ExportLimits
) -> tuple[datetime, datetime]:
    """Return the period to export: the filter's, or the last ``max_days`` up to now.

    Raises:
        ExportRefused: ``too_many_days`` when the filter's period is longer than allowed.
    """
    end = until or now
    start = since or end - timedelta(days=limits.max_days)
    check_span(end - start, limits)
    return start, end


def safe_cell(value: Cell) -> str:
    """Return ``value`` as a cell a spreadsheet will not run as a formula.

    A number is written as it is, a negative one too (a signed duration): it is never text a
    spreadsheet could run. Only text that starts like a formula gets the leading quote.
    """
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def to_csv(header: Sequence[str], rows: Iterable[Sequence[Cell]]) -> str:
    """Write ``rows`` as the export file: BOM, ``header``, one line per row."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    return BOM + out.getvalue()


def checksum(document: str) -> str:
    """Return the SHA-256 (hex) of the file as it is sent: the UTF-8 bytes of ``document``."""
    return hashlib.sha256(document.encode("utf-8")).hexdigest()
