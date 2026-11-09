"""CSV export of the usage reports: the screens' tables and the tidy hourly file (ADR 0117).

    authorise ``reports.export`` (denial audited) ─► the same load and builders as the screens
    ─► rows (small cells hidden) ─► limits ─► ``to_csv`` ─► SHA-256 ─► audit ``reports.export``

Two files, both inside the scope of ``reports.export`` and both built from the suppressed figures:
the **screen CSV** (``screen_rows``, one report family) and the **tidy CSV**
``openplanner.usage/1`` (``tidy_rows``, one row per hourly cell). An export over a limit is
refused with a code and audited, never cut short. The audit event holds the permission, the
scope, the family or schema, the range, the row count and the checksum, and never a figure.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Permission, Principal
from app.domain.csv_export import (
    Cell,
    ExportLimits,
    ExportRefusal,
    ExportRefused,
    check_span,
    checksum,
    to_csv,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.usage_report_csv import (
    HIDDEN,
    SCREEN_HEADER,
    TIDY_HEADER,
    TIDY_VERSION,
    screen_rows,
    tidy_rows,
)
from app.domain.usage_report_service import BUILDERS, LoadedUsage, UsageReportService

EXPORT_EVENT = Permission.REPORTS_EXPORT.value
"""The audit action of every export of a report: the permission's own name."""

TIDY = "tidy"
"""The ``view`` value of the tidy file; the screen files use the report family's code."""


@dataclass(frozen=True)
class UsageFile:
    """A finished export, ready to send.

    Attributes:
        document: The CSV text, starting with the byte order mark.
        name: A file name without extension: what it is, whose, and the range.
        rows: Data rows in the file.
        sha256: The checksum of the file as sent, for ``X-Content-SHA256`` and the audit event.
    """

    document: str
    name: str
    rows: int
    sha256: str


class UsageExportService:
    """Builds the export files and audits each of them.

    Args:
        reports: Loads the cells inside the scope of a permission; the screens' own service.
        admin: Writes the audit event.
        limits: ``reporting_export_max_rows`` and ``_max_days``, read at each export (class C).
    """

    def __init__(
        self,
        reports: UsageReportService,
        admin: AdminService,
        limits: Callable[[], ExportLimits],
    ) -> None:
        """Store the collaborators."""
        self._reports = reports
        self._admin = admin
        self._limits = limits

    async def export(
        self,
        principal: Principal,
        view: str,
        *,
        organisation_slug: str | None = None,
        group_slug: str | None = None,
        first_day: date | None = None,
        last_day: date | None = None,
    ) -> UsageFile:
        """Return the file for ``view`` (a report family, or ``tidy``) and audit the export.

        Raises:
            DomainError: ``not_permitted`` (audited by the permission check),
                ``group_not_found``, ``invalid_range``, or ``invalid_range`` for a ``view`` that
                is neither a report family nor ``tidy``.
            ExportRefused: ``too_many_days`` or ``too_many_rows``; the refusal is audited.
        """
        if view != TIDY and view not in BUILDERS:
            raise DomainError(ErrorCode.INVALID_RANGE)
        loaded = await self._reports.load(
            principal,
            organisation_slug,
            group_slug,
            first_day,
            last_day,
            Permission.REPORTS_EXPORT,
        )
        limits = self._limits()
        try:
            check_span(timedelta(days=(loaded.last - loaded.first).days + 1), limits)
            header, rows, hidden = self._table(view, loaded)
            if len(rows) > limits.max_rows:
                raise ExportRefused(ExportRefusal.TOO_MANY_ROWS)
        except ExportRefused as refused:
            await self._audit(principal, view, loaded, Outcome.FAILURE, refused.code)
            raise
        document = to_csv(header, rows)
        file = UsageFile(document, self._name(view, loaded), len(rows), checksum(document))
        await self._audit(principal, view, loaded, Outcome.SUCCESS, "", file, hidden)
        return file

    @staticmethod
    def _table(
        view: str, loaded: LoadedUsage
    ) -> tuple[tuple[str, ...], Sequence[Sequence[Cell]], int]:
        """Return the header, the rows and the number of hidden cells of one file."""
        if view == TIDY:
            tidy = tidy_rows(
                loaded.cells, loaded.groups, loaded.first, loaded.last, loaded.threshold
            )
            return TIDY_HEADER, tidy.rows, tidy.hidden
        rows = screen_rows(BUILDERS[view](loaded))
        return SCREEN_HEADER, rows, sum(1 for row in rows if row[4] == HIDDEN)

    @staticmethod
    def _name(view: str, loaded: LoadedUsage) -> str:
        """Return the file name: the schema or family, the group or organisation, the range."""
        what = TIDY_VERSION.replace("/", "-") if view == TIDY else f"usage-{view}"
        who = loaded.groups[0].slug if len({g.group_id for g in loaded.groups}) == 1 else None
        who = who or loaded.groups[0].organisation_slug
        return f"{what}-{who}-{loaded.first:%Y%m%d}-{loaded.last:%Y%m%d}"

    async def _audit(
        self,
        principal: Principal,
        view: str,
        loaded: LoadedUsage,
        outcome: Outcome,
        reason: str,
        file: UsageFile | None = None,
        hidden: int = 0,
    ) -> None:
        """Audit an export at the narrowest level that covers it: codes and counts only."""
        one = loaded.groups[0] if len(loaded.groups) == 1 else None
        detail = f"view:{view} from:{loaded.first} to:{loaded.last}"
        if file is not None:
            detail += f" rows:{file.rows} hidden:{hidden} sha256:{file.sha256}"
        await self._admin.record(
            principal.subject,
            EXPORT_EVENT,
            outcome,
            reason,
            organisation=loaded.groups[0].organisation_slug,
            group=one.slug if one else None,
            detail=detail,
        )
