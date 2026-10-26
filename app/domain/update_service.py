"""Use cases of updates started from the admin portal (ADR 0028).

Three callers, three paths:

    worker         check_if_due ─► ReleaseSource.latest (at most hourly) ─► release_checks
    superuser      notice (banner) · overview · request_update · request_restore · cancel
                   (system.update, every change audited)
    host updater   claim ─► running ─► report(succeeded | rolled_back | failed, reason)
                   (through ``python -m app.cli``; audited as ``host-updater``)

The service never touches containers or images: it only records what is asked and what
happened. The host updater does the work and checks the image signature.
"""

from dataclasses import dataclass

from app.domain.admin import AdminService, record_change, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal, Target, decide
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, IntegrationError, ReleaseSource, Transaction, UnitOfWork
from app.domain.updates import (
    Release,
    ReleaseCheck,
    UpdateAction,
    UpdateRequest,
    UpdateStatus,
    Version,
    available_update,
    check_due,
    claimed,
    finished,
    open_request,
    recent_outcome,
    restore_candidate,
    unclaimed,
)

UPDATER_ACTOR = "host-updater"
"""Audit actor of the steps the host updater reports."""

_PERMISSION = Permission.SYSTEM_UPDATE


@dataclass(frozen=True)
class UpdateNotice:
    """What the banner on every admin page shows a superuser.

    Attributes:
        available: A newer release, when no request is open.
        outcome: The latest finished request, for a day.
        open: The request that is waiting or running.
    """

    available: Release | None
    outcome: UpdateRequest | None
    open: UpdateRequest | None


@dataclass(frozen=True)
class UpdateOverview:
    """What the updates page shows.

    Attributes:
        enabled: Whether the installation checks for releases (``UPDATE_REPOSITORY``).
        current: The running version; ``None`` for a build that is not a release.
        check: The latest release check.
        available: A newer release that can be requested now.
        open: The request that is waiting or running.
        unclaimed: Whether ``open`` has waited so long that no updater seems installed.
        restore: The update whose previous version can be restored now.
        history: The newest requests, newest first.
    """

    enabled: bool
    current: Version | None
    check: ReleaseCheck | None
    available: Release | None
    open: UpdateRequest | None
    unclaimed: bool
    restore: UpdateRequest | None
    history: list[UpdateRequest]


class UpdateService:
    """Application service of updates.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises the superuser and audits refusals.
        current: The running version (``APP_VERSION``); ``None`` when it is not a release.
        releases: Where releases are looked up; ``None`` when ``UPDATE_REPOSITORY`` is unset.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        *,
        current: Version | None,
        releases: ReleaseSource | None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._current = current
        self._releases = releases

    @property
    def current(self) -> Version | None:
        """The running version."""
        return self._current

    # --- worker ------------------------------------------------------------------------

    async def check_if_due(self) -> bool:
        """Look up the latest release when the last check is an hour old; return if it did.

        A failed lookup is stored as an error code next to the release found before, so the
        page can say the check fails without forgetting what is known.
        """
        if self._releases is None:
            return False
        now = self._clock.now()
        async with self._uow.read() as tx:
            last = await tx.get_release_check()
        if not check_due(last, now):
            return False
        try:
            check = ReleaseCheck(now, await self._releases.latest())
        except IntegrationError as error:
            check = ReleaseCheck(now, last.latest if last else None, error.code)
        async with self._uow.locked() as tx:
            await tx.save_release_check(check)
        return True

    # --- superuser ---------------------------------------------------------------------

    async def notice(self, principal: Principal) -> UpdateNotice | None:
        """Return the banner for ``principal``, or ``None`` when there is nothing to show.

        Decided by the matrix without auditing: the banner is asked for on every page, and
        not seeing it is not a refused request.
        """
        if not decide(principal, _PERMISSION, Target.everything()).allowed:
            return None
        now = self._clock.now()
        async with self._uow.read() as tx:
            check = await tx.get_release_check()
            history = await tx.get_update_requests()
        current = open_request(history)
        notice = UpdateNotice(
            None if current else available_update(self._current, check),
            recent_outcome(history, now),
            current,
        )
        return notice if (notice.available or notice.outcome or notice.open) else None

    async def overview(self, principal: Principal) -> UpdateOverview:
        """Return everything the updates page shows.

        Raises:
            DomainError: ``not_permitted``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        now = self._clock.now()
        async with self._uow.read() as tx:
            check = await tx.get_release_check()
            history = await tx.get_update_requests()
        current = open_request(history)
        return UpdateOverview(
            enabled=self._releases is not None,
            current=self._current,
            check=check,
            available=None if current else available_update(self._current, check),
            open=current,
            unclaimed=current is not None and unclaimed(current, now),
            restore=None if current else restore_candidate(history, self._current, now),
            history=history,
        )

    async def request_update(self, principal: Principal) -> UpdateRequest:
        """Ask the host updater to move to the latest release.

        Raises:
            DomainError: ``not_permitted``; ``update_in_progress`` while a request is open;
                ``update_not_available`` without a newer release (or on a development build).
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        async with self._uow.locked() as tx:
            await _refuse_when_open(tx)
            release = available_update(self._current, await tx.get_release_check())
            if release is None or self._current is None:
                raise DomainError(ErrorCode.UPDATE_NOT_AVAILABLE)
            return await self._add(tx, principal, UpdateAction.UPDATE, release.version)

    async def request_restore(self, principal: Principal) -> UpdateRequest:
        """Ask the host updater to go back to the version the latest update replaced.

        Raises:
            DomainError: ``not_permitted``; ``update_in_progress`` while a request is open;
                ``update_not_available`` outside the restore window.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        async with self._uow.locked() as tx:
            await _refuse_when_open(tx)
            update = restore_candidate(
                await tx.get_update_requests(), self._current, self._clock.now()
            )
            if update is None:
                raise DomainError(ErrorCode.UPDATE_NOT_AVAILABLE)
            return await self._add(tx, principal, UpdateAction.RESTORE, update.from_version)

    async def cancel(self, principal: Principal, request_id: int) -> UpdateRequest:
        """Withdraw a request the host updater has not claimed yet.

        Raises:
            DomainError: ``not_permitted``; ``update_request_not_found``;
                ``update_in_progress`` once it is running or finished.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        now = self._clock.now()
        async with self._uow.locked() as tx:
            ended = finished(await _request(tx, request_id), UpdateStatus.CANCELLED, "", now)
            await tx.save_update_request(ended)
            await record_change(tx, now, principal, _PERMISSION, None, None, "request.cancel")
        return ended

    async def _add(
        self, tx: Transaction, principal: Principal, action: UpdateAction, target: Version
    ) -> UpdateRequest:
        """Store a new request from the running version to ``target`` and audit it."""
        if self._current is None:  # pragma: no cover - callers checked it
            raise DomainError(ErrorCode.UPDATE_NOT_AVAILABLE)
        now = self._clock.now()
        created = await tx.add_update_request(
            UpdateRequest(
                id=None,
                action=action,
                target=target,
                from_version=self._current,
                requested_by=principal.subject,
                requested_at=now,
                status=UpdateStatus.REQUESTED,
            )
        )
        await record_change(tx, now, principal, _PERMISSION, None, None, f"{action}.request")
        return created

    # --- host updater ------------------------------------------------------------------

    async def claim(self) -> UpdateRequest | None:
        """Mark the oldest waiting request as running and return it; ``None`` if none waits."""
        now = self._clock.now()
        async with self._uow.locked() as tx:
            waiting = [
                r for r in await tx.get_update_requests() if r.status is UpdateStatus.REQUESTED
            ]
            if not waiting:
                return None
            started = claimed(waiting[-1], now)
            await tx.save_update_request(started)
            await _audit_updater(tx, started, Outcome.SUCCESS, f"{started.action}.start")
        return started

    async def report(self, request_id: int, status: UpdateStatus, reason: str) -> UpdateRequest:
        """Record the outcome of a running request.

        Success is only believed from the version it promised: this call runs in the
        container the updater started, so a "succeeded" from another version is recorded as
        ``failed`` with ``version_mismatch``.

        Raises:
            DomainError: ``update_request_not_found``; ``update_in_progress`` when it is not
                running; ``invalid_configuration`` for a reason that is not a code.
        """
        now = self._clock.now()
        async with self._uow.locked() as tx:
            running = await _request(tx, request_id)
            if status is UpdateStatus.SUCCEEDED and running.target != self._current:
                status, reason = UpdateStatus.FAILED, "version_mismatch"
            ended = finished(running, status, reason, now)
            await tx.save_update_request(ended)
            outcome = Outcome.SUCCESS if status is UpdateStatus.SUCCEEDED else Outcome.FAILURE
            await _audit_updater(tx, ended, outcome, f"{ended.action}.{status}")
        return ended


async def _refuse_when_open(tx: Transaction) -> None:
    """Raise ``update_in_progress`` while a request is waiting or running."""
    if open_request(await tx.get_update_requests()) is not None:
        raise DomainError(ErrorCode.UPDATE_IN_PROGRESS)


async def _request(tx: Transaction, request_id: int) -> UpdateRequest:
    """Return the request with ``request_id`` among the recent ones.

    Raises:
        DomainError: ``update_request_not_found``.
    """
    found = next((r for r in await tx.get_update_requests() if r.id == request_id), None)
    if found is None:
        raise DomainError(ErrorCode.UPDATE_REQUEST_NOT_FOUND)
    return found


async def _audit_updater(
    tx: Transaction, request: UpdateRequest, outcome: Outcome, detail: str
) -> None:
    """Record a step of the host updater (global: only the superuser sees it)."""
    event = AuditEvent(
        request.finished_at or request.started_at or request.requested_at,
        UPDATER_ACTOR,
        _PERMISSION.value,
        outcome,
        request.reason,
        detail=detail,
    )
    await tx.add_audit_event(event)
