"""Use cases of updates started from the admin portal (ADR 0029).

Three callers, three paths:

    worker         check_if_due ─► ReleaseSource.latest (once per interval, or when asked)
                   ─► release_checks
    superuser      notice (banner) · overview · request_check · request_update ·
                   request_restore · cancel (system.update, every change audited)
    overview page  summary: the notice without a principal, for the *System status* widget
                   (its route carries settings.platform.view, ADR 0061 §2)
    host updater   claim ─► running ─► report_step(step)* ─► report(succeeded | rolled_back
                   | failed, reason) (through ``python -m app.cli``; audited as ``host-updater``)

The service never touches containers or images: it only records what is asked and what
happened. The host updater does the work and checks the image signature.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from app.domain.admin import AdminService, record_change, require
from app.domain.audit import AuditEntry, AuditEvent, AuditFilter, Outcome, Source
from app.domain.authorization import Permission, Principal, Scope, Target, decide
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, IntegrationError, ReleaseSource, Transaction, UnitOfWork
from app.domain.update_policy import (
    ANY_TIME,
    UpdateWindow,
    auto_apply_effective,
    claimable,
    line_support,
    on_line,
)
from app.domain.updates import (
    CHECK_INTERVAL,
    HISTORY_ACTIONS,
    HISTORY_DETAILS,
    HOST_ACTOR,
    STALLED_AFTER,
    STALLED_REASON,
    VERSION_ACTION,
    ListedRelease,
    Release,
    ReleaseCheck,
    RunningVersion,
    SupportLine,
    UpdateAction,
    UpdateChannel,
    UpdateRequest,
    UpdateStatus,
    UpdateStep,
    Version,
    available_update,
    check_due,
    claimed,
    detail_key,
    finished,
    offered,
    on_channel,
    open_request,
    progressed,
    recent_outcome,
    restore_candidate,
    stalled,
    unclaimed,
    version_changed,
)

UPDATER_ACTOR = "host-updater"
"""Audit actor of the steps the host updater reports."""

SYSTEM_ACTOR = "system"
"""Audit actor of an update the worker requested itself (``UPDATE_AUTO_APPLY``, ADR 0059)."""

_PERMISSION = Permission.SYSTEM_UPDATE


def _constant[T](value: T) -> Callable[[], T]:
    """Return a callable that always returns ``value``."""
    return lambda: value


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
        channel: What it follows: releases only, or release candidates too (ADR 0059).
        auto_apply: Whether the worker requests a newer version itself.
        interval: How often the worker checks (``update_check_minutes``).
        current: The running version; ``None`` for a build that is not a release.
        check: The latest release check.
        check_requested: Whether a superuser asked for a check the worker has not run yet.
        newest: The newest version on the channel, from the stored listing; ``None`` when
            nothing is listed (the page then shows the check's latest, if any).
        available: A newer release that can be requested now.
        open: The request that is waiting or running.
        unclaimed: Whether ``open`` has waited so long that no updater seems installed.
        restore: The update whose previous version can be restored now.
        history: The newest requests, newest first.
        offered: The listed versions newer than the running one on the channel, newest
            first, with their notes (ADR 0067 point 3); empty while a request is open.
        outside_change: The running version when it came from outside the portal (installer,
            by hand) and is the latest change (ADR 0067 point 6); else ``None``.
        line: The minor followed (``update_line``, ADR 0116); empty for everything.
        window: When automatic updates happen (``update_window``).
        next_window: When the window opens next (now when it is open); ``None`` without one.
        support: The running line's entry of ``support.json``; ``None`` when unknown.
    """

    enabled: bool
    channel: UpdateChannel
    auto_apply: bool
    current: Version | None
    check: ReleaseCheck | None
    newest: Release | None
    available: Release | None
    open: UpdateRequest | None
    unclaimed: bool
    restore: UpdateRequest | None
    history: list[UpdateRequest]
    outside_change: RunningVersion | None = None
    offered: list[ListedRelease] = field(default_factory=list)
    interval: timedelta = CHECK_INTERVAL
    check_requested: bool = False
    line: str = ""
    window: UpdateWindow = ANY_TIME
    next_window: datetime | None = None
    support: SupportLine | None = None


class UpdateService:
    """Application service of updates.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises the superuser and audits refusals.
        current: The running version (``APP_VERSION``); ``None`` when it is not a release.
        releases: Where releases are looked up; ``None`` when ``UPDATE_REPOSITORY`` is unset
            and in the web process, which leaves the lookup to the worker.
        checking: Whether the installation checks for releases (``UPDATE_REPOSITORY``), also
            when this process does not look them up itself; defaults to ``releases`` being set.
        channel: ``release`` offers releases only; ``rc`` release candidates too (ADR 0059);
            a value, or a callable read on every use so a portal change applies live
            (ADR 0067).
        auto_apply: Whether a check that finds a newer version requests the update itself;
            a value or a callable, as ``channel``.
        interval: How often the worker checks for releases (``update_check_minutes``); a
            value or a callable, as ``channel``.
        stalled_after: How long a running request may stay silent before the application ends
            it as ``failed`` (``update_stalled_minutes``); a value or a callable, as ``channel``.
        line: ``update_line`` (ADR 0116 point 9): ``X.Y`` offers that minor's patches only;
            empty offers everything on the channel. A value or a callable, as ``channel``.
        window: ``update_window`` in the installation's zone: when the worker requests and
            the updater claims an automatic update. A value or a callable, as ``channel``.
        production: Whether ``ENVIRONMENT=prod``: auto-apply then needs a window
            (``auto_apply_effective``), and an automatic request is never claimed without one.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        *,
        current: Version | None,
        releases: ReleaseSource | None,
        checking: bool | None = None,
        channel: UpdateChannel | Callable[[], UpdateChannel] = UpdateChannel.RELEASE,
        auto_apply: bool | Callable[[], bool] = False,
        interval: timedelta | Callable[[], timedelta] = CHECK_INTERVAL,
        stalled_after: timedelta | Callable[[], timedelta] = STALLED_AFTER,
        line: str | Callable[[], str] = "",
        window: UpdateWindow | Callable[[], UpdateWindow] = ANY_TIME,
        production: bool = False,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._current = current
        self._releases = releases
        self._checking = releases is not None if checking is None else checking
        self._channel = channel if callable(channel) else _constant(channel)
        self._auto_apply = auto_apply if callable(auto_apply) else _constant(auto_apply)
        self._interval = interval if callable(interval) else _constant(interval)
        self._stalled_after = stalled_after if callable(stalled_after) else _constant(stalled_after)
        self._line = line if callable(line) else _constant(line)
        self._window = window if callable(window) else _constant(window)
        self._production = production

    @property
    def current(self) -> Version | None:
        """The running version."""
        return self._current

    # --- worker ------------------------------------------------------------------------

    async def check_if_due(self) -> bool:
        """Look up the latest release when the interval has passed or a superuser asked.

        Returns whether it did. A failed lookup is stored as an error code next to the
        release found before, so the page can say the check fails without forgetting what is
        known. With ``auto_apply``, a newer version becomes an update request at once (actor
        ``system``, audited), unless a request is already open (ADR 0059 point 6).
        """
        await self.end_stalled()
        if self._releases is None:
            return False
        now = self._clock.now()
        async with self._uow.read() as tx:
            last = await tx.get_release_check()
        if not check_due(last, now, self._interval()):
            return False
        listing: list[ListedRelease] | None
        known = last.support if last else None
        try:
            listing = await self._releases.listing()
            ranked = sorted(listing, key=lambda item: item.version, reverse=True)
            check = ReleaseCheck(
                now, ranked[0].release if ranked else None, support=await self._support(known)
            )
        except IntegrationError as error:
            listing = None
            check = ReleaseCheck(now, last.latest if last else None, error.code, support=known)
        async with self._uow.locked() as tx:
            await tx.save_release_check(check)
            if listing is not None:
                await tx.save_release_listing(listing)
            release = self._available(check, listing or await tx.get_release_listing())
            automatic = self._automatic() and self._window().is_open(now)
            if automatic and release is not None and self._current is not None:
                if open_request(await tx.get_update_requests()) is None:
                    await self._add(tx, None, UpdateAction.UPDATE, release.version)
        return True

    async def end_stalled(self) -> UpdateRequest | None:
        """End a running request whose updater went silent, as ``failed`` (``updater_stalled``).

        A request the host updater claimed but never finished (the disk was full, the host
        restarted) stays ``running`` and blocks every later request, so no update arrives any
        more. The worker and the updater's claim call this; a request that is still being
        carried out reports a step well within ``update_stalled_minutes``. Returns the ended
        request, or ``None`` when nothing was stalled.
        """
        now = self._clock.now()
        limit = self._stalled_after()
        async with self._uow.read() as tx:
            running = next(
                (r for r in await tx.get_update_requests() if stalled(r, now, limit)), None
            )
        if running is None:
            return None
        async with self._uow.locked() as tx:
            # Looked at again under the write lock: the updater may have reported meanwhile.
            current = next((r for r in await tx.get_update_requests() if r.id == running.id), None)
            if current is None or not stalled(current, now, limit):
                return None
            ended = finished(current, UpdateStatus.FAILED, STALLED_REASON, now)
            await tx.save_update_request(ended)
            await _audit_updater(tx, ended, Outcome.FAILURE, f"{ended.action}.{ended.status}")
        return ended

    async def _support(
        self, known: tuple[SupportLine, ...] | None
    ) -> tuple[SupportLine, ...] | None:
        """Return the source's support lines, or what was known when it has none or fails.

        A missing or unreadable index is no failed check: the page keeps the last good one.
        """
        if self._releases is None:  # pragma: no cover - the caller checked it
            return known
        try:
            found = await self._releases.support()
        except IntegrationError:
            return known
        return found if found is not None else known

    def _automatic(self) -> bool:
        """Whether the worker requests updates itself (``auto_apply_effective``)."""
        return auto_apply_effective(self._auto_apply(), self._window(), self._production)

    def _offered(self, listing: Sequence[ListedRelease]) -> list[ListedRelease]:
        """Return the listed versions offered: newer, on the channel and on the line."""
        return on_line(offered(self._current, listing, self._channel()), self._line())

    def _available(
        self, check: ReleaseCheck | None, listing: Sequence[ListedRelease]
    ) -> Release | None:
        """Return the newest version the channel offers above the running one, if any.

        From the stored listing (ADR 0067); without one (an installation that never listed),
        from the check's latest as before.
        """
        choices = self._offered(listing)
        if choices:
            return choices[0].release
        if listing:
            return None
        found = available_update(self._current, check, self._channel())
        line = self._line()
        return found if found is not None and line in ("", found.version.line) else None

    # --- superuser ---------------------------------------------------------------------

    async def request_check(self, principal: Principal) -> None:
        """Ask the worker to check for releases at its next poll instead of at the interval.

        The web process never asks GitHub itself (ADR 0029): the request is a mark on the
        stored check, which the worker's next check clears. Before the first check there is
        nothing to mark; the worker checks on its next poll anyway.

        Raises:
            DomainError: ``not_permitted``; ``update_not_available`` when the installation
                does not check for releases (no ``UPDATE_REPOSITORY``).
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        if not self._checking:
            raise DomainError(ErrorCode.UPDATE_NOT_AVAILABLE)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            last = await tx.get_release_check()
            if last is not None and last.requested_at is None:
                await tx.save_release_check(replace(last, requested_at=now))
            await record_change(tx, now, principal, _PERMISSION, None, None, "check.request")

    async def notice(self, principal: Principal) -> UpdateNotice | None:
        """Return the banner for ``principal``, or ``None`` when there is nothing to show.

        Decided by the matrix without auditing: the banner is asked for on every page, and
        not seeing it is not a refused request.
        """
        if not decide(principal, _PERMISSION, Target.everything()).allowed:
            return None
        notice = await self.summary()
        return notice if (notice.available or notice.outcome or notice.open) else None

    async def summary(self) -> UpdateNotice:
        """Return what is known about updates: a newer release, the open request, the outcome.

        No permission is checked here: the caller's route carries one (the *System status*
        widget needs ``settings.platform.view``), and nothing here names a secret.
        """
        now = self._clock.now()
        async with self._uow.read() as tx:
            check = await tx.get_release_check()
            history = await tx.get_update_requests()
            listing = await tx.get_release_listing()
        current = open_request(history)
        return UpdateNotice(
            None if current else self._available(check, listing),
            recent_outcome(history, now),
            current,
        )

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
            versions = await tx.get_running_versions(2)
            listing = await tx.get_release_listing()
        current = open_request(history)
        outside = versions[0] if len(versions) > 1 and versions[0].request_id is None else None
        on_this_channel = on_line(on_channel(listing, self._channel()), self._line())
        window = self._window()
        return UpdateOverview(
            enabled=self._checking,
            channel=self._channel(),
            auto_apply=self._automatic(),
            interval=self._interval(),
            current=self._current,
            check=check,
            check_requested=check is not None and check.requested_at is not None,
            newest=on_this_channel[0].release if on_this_channel else None,
            available=None if current else self._available(check, listing),
            open=current,
            unclaimed=current is not None and unclaimed(current, now),
            restore=None if current else restore_candidate(history, self._current, now),
            history=history,
            outside_change=outside,
            offered=[] if current else self._offered(listing),
            line=self._line(),
            window=window,
            next_window=window.next_opening(now),
            support=line_support(check.support if check and check.support else (), self._current),
        )

    async def history(self, principal: Principal, limit: int = 30) -> list[AuditEntry]:
        """Return the update history from the audit log: the milestones, newest first.

        Requests, outcomes, cancellations and version changes (``HISTORY_DETAILS``); the
        updater's steps stay in the audit log itself (ADR 0067 point 5).

        Raises:
            DomainError: ``not_permitted``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        narrow = AuditFilter(actions=HISTORY_ACTIONS)
        async with self._uow.read() as tx:
            entries = await tx.list_audit_events(Scope(everything=True), limit * 4, narrow)
        return [e for e in entries if detail_key(e.event.detail) in HISTORY_DETAILS][:limit]

    # --- web process, at start-up ----------------------------------------------------------

    async def note_running(self, version: str, commit: str) -> RunningVersion | None:
        """Record the running version when it differs from the last one seen (ADR 0067).

        Returns the new record, or ``None`` when nothing changed. A change the updater made
        (a running request for this version) is noted with its request; any other change
        is audited as actor ``host``: installer or by hand. The first record is never an
        event: there is nothing to compare it with.
        """
        now = self._clock.now()
        async with self._uow.locked() as tx:
            versions = await tx.get_running_versions(1)
            last = versions[0] if versions else None
            current = RunningVersion(version, commit, now)
            if not version_changed(last, current):
                return None
            running = open_request(await tx.get_update_requests())
            by_updater = (
                running is not None
                and running.status is UpdateStatus.RUNNING
                and str(running.target) == version
            )
            stored = await tx.add_running_version(
                RunningVersion(version, commit, now, running.id if by_updater and running else None)
            )
            if last is not None:
                event = AuditEvent(
                    now,
                    UPDATER_ACTOR if by_updater else HOST_ACTOR,
                    VERSION_ACTION,
                    Outcome.SUCCESS,
                    "",
                    detail=f"version.changed,from:{last.version},to:{version},commit:{commit[:12]}",
                    source=Source.SYSTEM,
                )
                await tx.add_audit_event(event)
        return stored

    async def request_update(
        self,
        principal: Principal,
        target: Version | None = None,
        apply_after: datetime | None = None,
    ) -> UpdateRequest:
        """Ask the host updater to move to ``target``, or to the newest version offered.

        ``target`` must be in the listing and newer than the running version (ADR 0067
        point 3: forward only; going back is ``request_restore``). With ``apply_after`` the
        updater takes it from that moment on (ADR 0116 point 9); a moment in the past is now.

        Raises:
            DomainError: ``not_permitted``; ``update_in_progress`` while a request is open;
                ``update_not_available`` without a newer release, for a target that is not
                offered, or on a development build.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        async with self._uow.locked() as tx:
            await _refuse_when_open(tx)
            if self._current is None:
                raise DomainError(ErrorCode.UPDATE_NOT_AVAILABLE)
            listing = await tx.get_release_listing()
            choices = self._offered(listing)
            if target is None:
                release = self._available(await tx.get_release_check(), listing)
                if release is None:
                    raise DomainError(ErrorCode.UPDATE_NOT_AVAILABLE)
                chosen = release.version
            else:
                if target not in {item.version for item in choices}:
                    raise DomainError(ErrorCode.UPDATE_NOT_AVAILABLE)
                chosen = target
            later = (
                apply_after.astimezone(UTC)
                if apply_after and apply_after > self._clock.now()
                else None
            )
            return await self._add(tx, principal, UpdateAction.UPDATE, chosen, later)

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
        self,
        tx: Transaction,
        principal: Principal | None,
        action: UpdateAction,
        target: Version,
        apply_after: datetime | None = None,
    ) -> UpdateRequest:
        """Store a new request from the running version to ``target`` and audit it.

        Without a principal the worker itself asks (``UPDATE_AUTO_APPLY``): the request and
        its audit event carry the actor ``system``.
        """
        if self._current is None:  # pragma: no cover - callers checked it
            raise DomainError(ErrorCode.UPDATE_NOT_AVAILABLE)
        now = self._clock.now()
        created = await tx.add_update_request(
            UpdateRequest(
                id=None,
                action=action,
                target=target,
                from_version=self._current,
                requested_by=principal.subject if principal else SYSTEM_ACTOR,
                requested_at=now,
                status=UpdateStatus.REQUESTED,
                apply_after=apply_after,
            )
        )
        versions = f"from:{self._current},to:{target}"
        if apply_after is not None:
            versions += f",after:{apply_after:%Y%m%dT%H%MZ}"
        if principal is None:
            event = AuditEvent(
                now,
                SYSTEM_ACTOR,
                _PERMISSION.value,
                Outcome.SUCCESS,
                "",
                detail=f"update.auto_request,{versions}",
                source=Source.SYSTEM,
            )
            await tx.add_audit_event(event)
        else:
            await record_change(
                tx, now, principal, _PERMISSION, None, None, f"{action}.request,{versions}"
            )
        return created

    # --- host updater ------------------------------------------------------------------

    async def claim(self) -> UpdateRequest | None:
        """Mark the oldest claimable request as running and return it; ``None`` if none is.

        A request waits for its ``apply_after``, and one the worker made itself for the
        window (ADR 0116 point 9, ``update_policy.claimable``).
        """
        await self.end_stalled()
        now = self._clock.now()
        window = self._window()
        async with self._uow.locked() as tx:
            waiting = [
                r
                for r in await tx.get_update_requests()
                if claimable(r, now, window, production=self._production)
            ]
            if not waiting:
                return None
            started = claimed(waiting[-1], now)
            await tx.save_update_request(started)
            await _audit_updater(tx, started, Outcome.SUCCESS, f"{started.action}.start")
        return started

    async def report_step(self, request_id: int, step: UpdateStep) -> UpdateRequest:
        """Record the step a running request has reached (ADR 0048); a repeat changes nothing.

        Raises:
            DomainError: ``update_request_not_found``; ``update_in_progress`` when it is not
                running; ``invalid_configuration`` for a step outside its plan or going back.
        """
        now = self._clock.now()
        async with self._uow.locked() as tx:
            running = await _request(tx, request_id)
            moved = progressed(running, step, now)
            if moved != running:
                await tx.save_update_request(moved)
                await _audit_updater(tx, moved, Outcome.SUCCESS, f"{moved.action}.{step}")
        return moved

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
    """Record a step of the host updater (global: only the superuser sees it).

    The detail names the versions too (``from``, ``to``), so the history can be read from
    the audit log alone (ADR 0067).
    """
    event = AuditEvent(
        request.finished_at or request.step_at or request.started_at or request.requested_at,
        UPDATER_ACTOR,
        _PERMISSION.value,
        outcome,
        request.reason,
        detail=f"{detail},from:{request.from_version},to:{request.target}",
    )
    await tx.add_audit_event(event)
