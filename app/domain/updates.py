"""Updates started from the admin portal (ADR 0029): versions, the release check and requests.

The worker checks the latest release at most once per ``CHECK_INTERVAL``. A superuser turns
an available release into a request; the host updater claims it, carries it out and reports
the outcome. A successful update can be restored to the version it replaced for
``RESTORE_WINDOW``.

    requested ──claim──► running ──report──► succeeded | rolled_back | failed
        └──cancel──► cancelled

While running, the updater reports each step it starts (ADR 0048), so the portal can show
where it is: ``PLANS`` lists the steps per action; rolling back may follow any of them.

Everything here is about the installation, not about citizens: no personal data.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from functools import total_ordering

from app.domain.errors import DomainError, ErrorCode

CHECK_INTERVAL = timedelta(minutes=60)
"""How often the worker asks for the latest release."""

RESTORE_WINDOW = timedelta(hours=24)
"""How long after a successful update the previous version can be restored."""

UNCLAIMED_AFTER = timedelta(minutes=10)
"""After this, a request nobody claimed is shown as "no updater has picked it up"."""

OUTCOME_SHOWN = timedelta(hours=24)
"""How long the banner reports the outcome of the latest request."""

SNOOZE = timedelta(minutes=60)
"""How long "remind me later" hides the banner."""

VERSION_ACTION = "system.version"
"""Audit action of a version change the web process noticed at start-up (ADR 0065)."""

HOST_ACTOR = "host"
"""Audit actor of a version change nobody requested in the portal: installer or by hand."""

HISTORY_ACTIONS = frozenset({"system.update", VERSION_ACTION})
"""The audit actions the updates page shows as its history (ADR 0065 point 5)."""

HISTORY_DETAILS = frozenset(
    {
        "update.request",
        "update.auto_request",
        "update.succeeded",
        "update.failed",
        "update.rolled_back",
        "restore.request",
        "restore.succeeded",
        "restore.failed",
        "restore.rolled_back",
        "request.cancel",
        "version.changed",
    }
)
"""The milestones the history lists; the updater's steps stay in the audit log only."""

_VERSION = re.compile(
    r"^v?(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})(?:-rc\.([1-9]\d{0,3}))?$"
)
_REASON = re.compile(r"^[a-z_]{1,40}$")
_RELEASE_PAGE = "https://github.com/"


class UpdateChannel(StrEnum):
    """What an installation follows (ADR 0059): releases only, or release candidates too."""

    RELEASE = "release"
    CANDIDATE = "rc"


@total_ordering
@dataclass(frozen=True)
class Version:
    """A release version (``MAJOR.MINOR.PATCH``) or a release candidate of one (``-rc.N``).

    Ordered as SemVer 2.0.0 orders them: ``0.4.0-rc.3 < 0.4.0`` and ``0.4.0-rc.3 > 0.3.0``;
    candidates of one version order by their number (ADR 0059).

    Attributes:
        major: MAJOR.
        minor: MINOR.
        patch: PATCH.
        candidate: ``N`` of ``-rc.N``; ``None`` for a release.
    """

    major: int
    minor: int
    patch: int
    candidate: int | None = None

    @classmethod
    def parse(cls, raw: str) -> "Version | None":
        """Return the version of ``1.4.0``, ``v1.4.0`` or ``1.4.0-rc.2``; ``None`` otherwise.

        Branch names (``main``), local builds and any other pre-release form are not
        versions: such an installation is never offered an update.
        """
        match = _VERSION.fullmatch(raw)
        if match is None:
            return None
        major, minor, patch, candidate = match.groups()
        return cls(int(major), int(minor), int(patch), int(candidate) if candidate else None)

    @property
    def is_candidate(self) -> bool:
        """Whether this is a release candidate rather than a release."""
        return self.candidate is not None

    def _key(self) -> tuple[int, int, int, int, int]:
        """Return the sort key: a release sorts above every candidate of its number."""
        if self.candidate is None:
            return (self.major, self.minor, self.patch, 1, 0)
        return (self.major, self.minor, self.patch, 0, self.candidate)

    def __lt__(self, other: object) -> bool:
        """Order as SemVer 2.0.0 does."""
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __str__(self) -> str:
        """Return ``MAJOR.MINOR.PATCH`` or ``MAJOR.MINOR.PATCH-rc.N``, without the tag's ``v``."""
        release = f"{self.major}.{self.minor}.{self.patch}"
        return release if self.candidate is None else f"{release}-rc.{self.candidate}"


@dataclass(frozen=True)
class Release:
    """A published release: its version and its release page (the notes).

    Raises:
        DomainError: ``invalid_configuration`` for ``url`` when it is not a GitHub https
            page; the link is shown to admins, so nothing else may get in.
    """

    version: Version
    url: str

    def __post_init__(self) -> None:
        """Refuse a release page that is not on GitHub."""
        if not self.url.startswith(_RELEASE_PAGE) or len(self.url) > 300:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "url")


@dataclass(frozen=True)
class RunningVersion:
    """A version this installation ran, as the web process saw it at start-up (ADR 0065).

    Attributes:
        version: ``APP_VERSION`` as text (``0.4.0-rc.7``, or ``main`` or empty for a build
            that is no release).
        commit: ``APP_COMMIT``; empty for a local build.
        since: When this version was first seen.
        request_id: The update request whose switch brought it, if the updater did; ``None``
            for a change outside the portal (installer, by hand).
    """

    version: str
    commit: str
    since: datetime
    request_id: int | None = None


def version_changed(previous: "RunningVersion | None", current: RunningVersion) -> bool:
    """Return whether ``current`` differs from ``previous`` in version or commit."""
    return previous is None or (previous.version, previous.commit) != (
        current.version,
        current.commit,
    )


def detail_key(detail: str) -> str:
    """Return the first key of an audit detail (``update.request`` of ``update.request,to:…``)."""
    return detail.split(",", 1)[0]


def detail_fields(detail: str) -> dict[str, str]:
    """Return the ``key:value`` parts of an audit detail, in order."""
    fields: dict[str, str] = {}
    for part in detail.split(",")[1:]:
        key, sep, value = part.partition(":")
        if sep:
            fields[key] = value
    return fields


@dataclass(frozen=True)
class ReleaseCheck:
    """The outcome of the latest release check.

    Attributes:
        checked_at: When the worker asked.
        latest: The latest release, kept from an earlier check when this one failed.
        error: The failure code of this check (``unauthorized``, ``server_error``, …), if any.
    """

    checked_at: datetime
    latest: Release | None
    error: str = ""


class UpdateAction(StrEnum):
    """What a request asks the host updater to do."""

    UPDATE = "update"
    RESTORE = "restore"


class UpdateStatus(StrEnum):
    """Where a request stands."""

    REQUESTED = "requested"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UpdateStep(StrEnum):
    """A step of the host updater, in the order it takes them.

    ``WAITING`` is not reported: it is the step of a request nobody claimed yet.
    """

    WAITING = "waiting"
    PULLING = "pulling"
    VERIFYING = "verifying"
    BACKING_UP = "backing_up"
    MIGRATING = "migrating"
    STARTING = "starting"
    ROLLING_BACK = "rolling_back"


PLANS: dict[UpdateAction, tuple[UpdateStep, ...]] = {
    UpdateAction.UPDATE: (
        UpdateStep.PULLING,
        UpdateStep.VERIFYING,
        UpdateStep.BACKING_UP,
        UpdateStep.STARTING,
    ),
    UpdateAction.RESTORE: (UpdateStep.BACKING_UP, UpdateStep.MIGRATING, UpdateStep.STARTING),
}
"""The steps the host updater reports per action (``scripts/updater``), in order."""

REPORTED_STEPS = frozenset(UpdateStep) - {UpdateStep.WAITING}
"""What the host updater can report."""

_ORDER = tuple(UpdateStep)


OUTCOMES = frozenset({UpdateStatus.SUCCEEDED, UpdateStatus.ROLLED_BACK, UpdateStatus.FAILED})
"""What the host updater can report."""


@dataclass(frozen=True)
class UpdateRequest:
    """A superuser's request to update or restore, and what became of it.

    Attributes:
        id: Database id; ``None`` before it is stored.
        action: Update to a newer release, or restore the version an update replaced.
        target: The version to run afterwards.
        from_version: The version running when it was requested.
        requested_by: Subject identifier of the superuser.
        requested_at: When it was requested.
        status: Where it stands.
        started_at: When the host updater claimed it.
        finished_at: When it ended (reported or cancelled).
        reason: The updater's code for a rollback or failure (``health_check_failed``, …).
        step: The step the updater reported last; ``None`` before its first report.
        step_at: When it reported that step.
    """

    id: int | None
    action: UpdateAction
    target: Version
    from_version: Version
    requested_by: str
    requested_at: datetime
    status: UpdateStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason: str = ""
    step: UpdateStep | None = None
    step_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        """Whether the request still waits for or is being carried out by the updater."""
        return self.status in (UpdateStatus.REQUESTED, UpdateStatus.RUNNING)


def check_due(last: ReleaseCheck | None, now: datetime) -> bool:
    """Return whether the worker should ask for the latest release now."""
    return last is None or now >= last.checked_at + CHECK_INTERVAL


def available_update(
    current: Version | None,
    check: ReleaseCheck | None,
    channel: UpdateChannel = UpdateChannel.RELEASE,
) -> Release | None:
    """Return the latest release if it is newer than the running release build.

    On the ``release`` channel a candidate is never offered, whatever the check found; on
    ``rc`` both are (ADR 0059 point 4).
    """
    if current is None or check is None or check.latest is None:
        return None
    if channel is UpdateChannel.RELEASE and check.latest.version.is_candidate:
        return None
    return check.latest if check.latest.version > current else None


def open_request(history: Sequence[UpdateRequest]) -> UpdateRequest | None:
    """Return the request that is requested or running, if any (there is at most one)."""
    return next((r for r in history if r.is_open), None)


def claimed(request: UpdateRequest, now: datetime) -> UpdateRequest:
    """Return the request as the host updater claimed it.

    Raises:
        DomainError: ``update_in_progress`` when it is not waiting to be claimed.
    """
    if request.status is not UpdateStatus.REQUESTED:
        raise DomainError(ErrorCode.UPDATE_IN_PROGRESS)
    return replace(request, status=UpdateStatus.RUNNING, started_at=now)


def finished(
    request: UpdateRequest, status: UpdateStatus, reason: str, now: datetime
) -> UpdateRequest:
    """Return the request ended with ``status``: an updater's outcome, or a cancellation.

    Raises:
        DomainError: ``update_in_progress`` for a transition the state machine does not
            have (an outcome for a request that is not running, cancelling a running one);
            ``invalid_configuration`` for ``reason`` when it is not a short code.
    """
    allowed = (
        request.status is UpdateStatus.RUNNING
        if status in OUTCOMES
        else status is UpdateStatus.CANCELLED and request.status is UpdateStatus.REQUESTED
    )
    if not allowed:
        raise DomainError(ErrorCode.UPDATE_IN_PROGRESS)
    if reason and not _REASON.fullmatch(reason):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "reason")
    return replace(request, status=status, reason=reason, finished_at=now)


def progressed(request: UpdateRequest, step: UpdateStep, now: datetime) -> UpdateRequest:
    """Return the running request at ``step``, as the host updater reported it.

    Steps only move forward; reporting the current step again changes nothing.

    Raises:
        DomainError: ``update_in_progress`` when the request is not running;
            ``invalid_configuration`` for ``step`` when it is not in the action's plan (or
            rolling back), or lies before the step reported last.
    """
    if request.status is not UpdateStatus.RUNNING:
        raise DomainError(ErrorCode.UPDATE_IN_PROGRESS)
    if step is request.step:
        return request
    allowed = (*PLANS[request.action], UpdateStep.ROLLING_BACK)
    if step not in allowed or (
        request.step is not None and _ORDER.index(step) < _ORDER.index(request.step)
    ):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "step")
    return replace(request, step=step, step_at=now)


@dataclass(frozen=True)
class PlanStep:
    """One line of the progress list.

    Attributes:
        step: The step.
        state: ``done``, ``current`` or ``pending``.
        since: When the current step began (claimed or reported); ``None`` otherwise.
    """

    step: UpdateStep
    state: str
    since: datetime | None = None


def progress(request: UpdateRequest) -> list[PlanStep]:
    """Return the steps of an open request with their state; empty once it ended.

    The waiting step comes first. A claimed request without a reported step (an updater or
    a running version from before ADR 0048) is shown at the first step of its plan. Rolling
    back is listed only once the updater reports it.
    """
    if not request.is_open:
        return []
    since: datetime | None
    if request.status is UpdateStatus.REQUESTED:
        current, since = UpdateStep.WAITING, request.requested_at
    else:
        current = request.step or PLANS[request.action][0]
        since = request.step_at or request.started_at
    steps = [UpdateStep.WAITING, *PLANS[request.action]]
    if current is UpdateStep.ROLLING_BACK:
        steps.append(UpdateStep.ROLLING_BACK)
    at = steps.index(current)
    return [
        PlanStep(step, "done")
        if index < at
        else PlanStep(step, "current", since)
        if index == at
        else PlanStep(step, "pending")
        for index, step in enumerate(steps)
    ]


def restore_candidate(
    history: Sequence[UpdateRequest], current: Version | None, now: datetime
) -> UpdateRequest | None:
    """Return the update whose previous version can be restored now, if any.

    That is the newest successful request, if it was an update, finished less than
    ``RESTORE_WINDOW`` ago and its version is the one running. ``history`` is newest first.
    """
    latest = next((r for r in history if r.status is UpdateStatus.SUCCEEDED), None)
    if (
        latest is None
        or latest.action is not UpdateAction.UPDATE
        or latest.target != current
        or latest.finished_at is None
        or now >= latest.finished_at + RESTORE_WINDOW
    ):
        return None
    return latest


def unclaimed(request: UpdateRequest, now: datetime) -> bool:
    """Return whether a request has waited so long that no updater seems to be installed."""
    return (
        request.status is UpdateStatus.REQUESTED and now >= request.requested_at + UNCLAIMED_AFTER
    )


def recent_outcome(history: Sequence[UpdateRequest], now: datetime) -> UpdateRequest | None:
    """Return the newest request the updater finished less than ``OUTCOME_SHOWN`` ago."""
    latest = next((r for r in history if r.status in OUTCOMES), None)
    if latest is None or latest.finished_at is None or now >= latest.finished_at + OUTCOME_SHOWN:
        return None
    return latest
