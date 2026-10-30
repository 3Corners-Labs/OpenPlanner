"""SQL queries of updates started from the admin portal (ADR 0029).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. Versions are stored as ``MAJOR.MINOR.PATCH`` text; a stored value that no longer
parses (a hand-edited row) makes the release unknown rather than failing the page.
"""

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    ReleaseCheckRow,
    ReleaseListingRow,
    RunningVersionRow,
    UpdateRequestRow,
)
from app.domain.errors import DomainError
from app.domain.updates import (
    ListedRelease,
    Release,
    ReleaseCheck,
    RunningVersion,
    UpdateAction,
    UpdateRequest,
    UpdateStatus,
    UpdateStep,
    Version,
)

_CHECK_ID = 1


def _version(raw: str) -> Version:
    """Parse a stored version; rows are written by this module, so it always parses."""
    version = Version.parse(raw)
    if version is None:  # pragma: no cover - only a hand-edited row
        raise ValueError("stored version does not parse")
    return version


def _release(version: str | None, url: str | None) -> Release | None:
    """Rebuild the stored release; anything that is no longer valid counts as unknown."""
    parsed = Version.parse(version or "")
    if parsed is None or url is None:
        return None
    try:
        return Release(parsed, url)
    except DomainError:
        return None


def _request(row: UpdateRequestRow) -> UpdateRequest:
    """Map a row to the domain."""
    return UpdateRequest(
        id=row.id,
        action=UpdateAction(row.action),
        target=_version(row.target_version),
        from_version=_version(row.from_version),
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        status=UpdateStatus(row.status),
        started_at=row.started_at,
        finished_at=row.finished_at,
        reason=row.reason,
        step=UpdateStep(row.step) if row.step else None,
        step_at=row.step_at,
    )


class UpdateQueries:
    """The updates' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def get_release_check(self) -> ReleaseCheck | None:
        """Return the outcome of the latest release check, if there was one."""
        row = await self._session.get(ReleaseCheckRow, _CHECK_ID)
        if row is None:
            return None
        return ReleaseCheck(
            row.checked_at, _release(row.latest_version, row.release_url), row.error
        )

    async def save_release_check(self, check: ReleaseCheck) -> None:
        """Replace the single release-check row."""
        await self._session.merge(
            ReleaseCheckRow(
                id=_CHECK_ID,
                checked_at=check.checked_at,
                latest_version=str(check.latest.version) if check.latest else None,
                release_url=check.latest.url if check.latest else None,
                error=check.error,
            )
        )
        await self._session.flush()

    async def get_release_listing(self) -> list[ListedRelease]:
        """Return the stored listing, newest first; rows that no longer parse are skipped."""
        rows = await self._session.scalars(select(ReleaseListingRow))
        listed = []
        for row in rows:
            version = Version.parse(row.version)
            if version is None:
                continue
            try:
                listed.append(ListedRelease(version, row.release_url, row.published_at, row.notes))
            except DomainError:
                continue
        return sorted(listed, key=lambda item: item.version, reverse=True)

    async def save_release_listing(self, listing: Sequence[ListedRelease]) -> None:
        """Replace the stored listing."""
        await self._session.execute(delete(ReleaseListingRow))
        for item in listing:
            self._session.add(
                ReleaseListingRow(
                    version=str(item.version),
                    release_url=item.url,
                    published_at=item.published_at,
                    notes=item.notes,
                )
            )
        await self._session.flush()

    async def get_running_versions(self, limit: int = 2) -> list[RunningVersion]:
        """Return the versions this installation ran, newest first."""
        rows = await self._session.scalars(
            select(RunningVersionRow).order_by(RunningVersionRow.id.desc()).limit(limit)
        )
        return [RunningVersion(row.version, row.commit, row.since, row.request_id) for row in rows]

    async def add_running_version(self, running: RunningVersion) -> RunningVersion:
        """Record a version seen at start-up."""
        self._session.add(
            RunningVersionRow(
                version=running.version[:20],
                commit=running.commit[:40],
                since=running.since,
                request_id=running.request_id,
            )
        )
        await self._session.flush()
        return running

    async def get_update_requests(self, limit: int = 20) -> list[UpdateRequest]:
        """Return the newest update requests, newest first."""
        rows = await self._session.scalars(
            select(UpdateRequestRow).order_by(UpdateRequestRow.id.desc()).limit(limit)
        )
        return [_request(row) for row in rows]

    async def add_update_request(self, request: UpdateRequest) -> UpdateRequest:
        """Store a new request and return it with its id."""
        row = UpdateRequestRow(
            action=request.action.value,
            target_version=str(request.target),
            from_version=str(request.from_version),
            requested_by=request.requested_by,
            requested_at=request.requested_at,
            status=request.status.value,
            started_at=request.started_at,
            finished_at=request.finished_at,
            reason=request.reason,
            step=request.step.value if request.step else None,
            step_at=request.step_at,
        )
        self._session.add(row)
        await self._session.flush()
        return _request(row)

    async def save_update_request(self, request: UpdateRequest) -> None:
        """Store the status, times, reason and step of an existing request."""
        row = await self._session.get(UpdateRequestRow, request.id)
        if row is None:  # pragma: no cover - the service read it in this transaction
            raise LookupError("update request not found")
        row.status = request.status.value
        row.started_at = request.started_at
        row.finished_at = request.finished_at
        row.reason = request.reason
        row.step = request.step.value if request.step else None
        row.step_at = request.step_at
        await self._session.flush()
