"""The usage API: the hourly figures of an API client's groups, paged (ADR 0117 point 13).

    API client (scope ``reports:read``) ─► the groups it may read (``ApiAccessService.groups``)
    ─► the hourly cells of those groups ─► ``tidy_rows`` (small cells left out, optional cut)
    ─► sorted rows ─► one page after a cursor, with ``as_of`` and an ETag

The rows are the ones of the tidy CSV ``openplanner.usage/1`` (columns ``TIDY_HEADER``), so
the file, the API and the screens share the suppression, the cut list and the scope code. A client
never sees a group outside its grant: an unknown or foreign ``group`` is ``group_not_found``,
the same answer. No personal data is read; the figures are counts and sums.

The cursor is the key of the last row sent (its identifying columns), so a page is stable while the
rollup adds cells; it carries no more than the client was shown. The ETag changes when the rollup
runs, the threshold changes or the request does, so a client can poll cheaply.
"""

import base64
import binascii
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.api_access import ApiAccessService
from app.domain.api_clients import ApiCaller
from app.domain.authorization import ApiScope, Scope
from app.domain.csv_export import Cell
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, UsageUnitOfWork
from app.domain.usage import METRICS
from app.domain.usage_report import MAX_DAYS
from app.domain.usage_report_csv import CUT_FIELDS, KEY_COLUMNS, row_key, tidy_rows
from app.domain.usage_suppress import effective_threshold

_MARGIN = timedelta(days=1)
"""Cells are read a day wider than the range on each side, as the screens do (zones)."""

METRIC_IDS: frozenset[str] = frozenset(m.id for m in METRICS if not m.staff)
"""The metrics the API serves; the team metrics have their own switch and are never here."""

SEPARATOR = "\x1f"
"""Joins the key columns inside a cursor; no code or slug contains it."""


@dataclass(frozen=True)
class UsageApiLimits:
    """The bounds of one call.

    Attributes:
        page_size: Most rows in one page (``reporting_api_page_size``).
        max_days: Widest range of one call (``reporting_export_max_days``).
    """

    page_size: int = 1000
    max_days: int = 400


@dataclass(frozen=True)
class UsagePage:
    """One page of rows and what a client needs to continue and to poll.

    Attributes:
        rows: The rows, in the order of ``TIDY_HEADER``.
        next_cursor: Pass it to get the next page; ``None`` at the end.
        as_of: When the rollup last ran, or ``None`` if it never did.
        etag: A validator for this exact request and state.
        hidden: How many cells of the range were left out as too small (a count, not their cuts).
    """

    rows: list[tuple[Cell, ...]]
    next_cursor: str | None
    as_of: datetime | None
    etag: str
    hidden: int


def encode_cursor(key: tuple[str, ...]) -> str:
    """Return the opaque cursor for the row with ``key``."""
    return base64.urlsafe_b64encode(SEPARATOR.join(key).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, ...]:
    """Return the key a cursor stands for.

    Raises:
        DomainError: ``invalid_range`` for text that is no cursor.
    """
    try:
        standard = cursor.replace("-", "+").replace("_", "/")
        raw = base64.b64decode(standard + "=" * (-len(cursor) % 4), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise DomainError(ErrorCode.INVALID_RANGE) from None
    key = tuple(raw.split(SEPARATOR))
    if len(key) != KEY_COLUMNS:
        raise DomainError(ErrorCode.INVALID_RANGE)
    return key


class UsageApiService:
    """Reads the usage figures for an API client inside its grant.

    Args:
        uow: Opens read transactions with the usage queries.
        access: Gives the groups a client may read with a scope.
        clock: Current time.
        platform_min: ``reporting_min_cell_size`` (class C, read each time).
        organisation_min: ``reporting_org_min_cell_size`` of an organisation id (class D).
        limits: Page size and widest range, read at each call (class C).
    """

    def __init__(
        self,
        uow: UsageUnitOfWork,
        access: ApiAccessService,
        clock: Clock,
        platform_min: Callable[[], int],
        organisation_min: Callable[[int], int],
        limits: Callable[[], UsageApiLimits],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._access = access
        self._clock = clock
        self._platform_min = platform_min
        self._organisation_min = organisation_min
        self._limits = limits

    async def read(
        self,
        caller: ApiCaller,
        *,
        first_day: date,
        last_day: date,
        metric: str | None = None,
        group: str | None = None,
        cut: str | None = None,
        cursor: str | None = None,
    ) -> UsagePage:
        """Return one page of the hourly figures of the caller's groups.

        ``cut`` keeps one cut column and adds the cells up over the others; without it every
        cut is added up (the duration bucket is always kept).

        Raises:
            DomainError: ``invalid_range`` for a backwards or too wide range, an unknown metric
                or cut, or a bad cursor; ``group_not_found`` for a group outside the grant.
        """
        limits = self._limits()
        if first_day > last_day or (last_day - first_day).days + 1 > min(limits.max_days, MAX_DAYS):
            raise DomainError(ErrorCode.INVALID_RANGE)
        if (metric is not None and metric not in METRIC_IDS) or (
            cut is not None and cut not in CUT_FIELDS
        ):
            raise DomainError(ErrorCode.INVALID_RANGE)
        after = decode_cursor(cursor) if cursor is not None else None
        allowed = await self._access.groups(caller, ApiScope.REPORTS_READ)
        if group is not None:
            allowed = [g for g in allowed if g.group.slug == group]
            if not allowed:
                raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        if not allowed:
            return self._page([], None, limits, None, 0, caller, "")
        scope = Scope(groups=frozenset((g.organisation_slug, g.group.slug) for g in allowed))
        async with self._uow.read() as tx:
            groups = await tx.list_report_groups(scope)
            if not groups:
                return self._page([], None, limits, None, 0, caller, "")
            lower = datetime.combine(first_day, datetime.min.time(), ZoneInfo("UTC")) - _MARGIN
            upper = datetime.combine(last_day, datetime.min.time(), ZoneInfo("UTC")) + 2 * _MARGIN
            cells = await tx.read_usage_cells(scope, [g.group_id for g in groups], lower, upper)
            state = await tx.usage_state()
        threshold = effective_threshold(
            self._platform_min(), self._organisation_min(groups[0].organisation_id)
        )
        tidy = tidy_rows(
            cells,
            groups,
            first_day,
            last_day,
            threshold,
            keep=frozenset() if cut is None else frozenset({cut}),
            metric=metric,
        )
        rows = [
            r for r in tidy.rows if r[3] in METRIC_IDS and (after is None or row_key(r) > after)
        ]
        shown, rest = rows[: limits.page_size], rows[limits.page_size :]
        token = encode_cursor(row_key(shown[-1])) if shown and rest else None
        grant = ",".join(sorted(g.group.slug for g in allowed))
        query = f"{first_day}|{last_day}|{metric}|{group}|{cut}|{cursor}|{threshold}|{grant}"
        return self._page(shown, token, limits, state.rolled_at, tidy.hidden, caller, query)

    @staticmethod
    def _page(
        rows: list[tuple[Cell, ...]],
        token: str | None,
        limits: UsageApiLimits,
        as_of: datetime | None,
        hidden: int,
        caller: ApiCaller,
        query: str,
    ) -> UsagePage:
        """Build a page and its ETag from the request and the rollup state."""
        tag = hashlib.sha256(
            f"{caller.client.client_id}|{as_of}|{limits.page_size}|{query}".encode()
        ).hexdigest()[:32]
        return UsagePage(rows, token, as_of, tag, hidden)
