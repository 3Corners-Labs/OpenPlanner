"""Updates started from the admin portal (ADR 0029): versions, the release check and requests.

The worker checks the latest release at most once per ``CHECK_INTERVAL``. A superuser turns
an available release into a request; the host updater claims it, carries it out and reports
the outcome. A successful update can be restored to the version it replaced for
``RESTORE_WINDOW``.

    requested ──claim──► running ──report──► succeeded | rolled_back | failed
        └──cancel──► cancelled

Everything here is about the installation, not about citizens: no personal data.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

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

_VERSION = re.compile(r"^v?(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$")
_REASON = re.compile(r"^[a-z_]{1,40}$")
_RELEASE_PAGE = "https://github.com/"


@dataclass(frozen=True, order=True)
class Version:
    """A release version (``MAJOR.MINOR.PATCH``), ordered numerically."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, raw: str) -> "Version | None":
        """Return the version of ``1.4.0`` or ``v1.4.0``; ``None`` for anything else.

        Pre-releases, branch names (``main``) and local builds are not versions: such an
        installation is never offered an update.
        """
        match = _VERSION.fullmatch(raw)
        if match is None:
            return None
        major, minor, patch = (int(part) for part in match.groups())
        return cls(major, minor, patch)

    def __str__(self) -> str:
        """Return ``MAJOR.MINOR.PATCH`` without the ``v`` of the tag."""
        return f"{self.major}.{self.minor}.{self.patch}"


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

    @property
    def is_open(self) -> bool:
        """Whether the request still waits for or is being carried out by the updater."""
        return self.status in (UpdateStatus.REQUESTED, UpdateStatus.RUNNING)


def check_due(last: ReleaseCheck | None, now: datetime) -> bool:
    """Return whether the worker should ask for the latest release now."""
    return last is None or now >= last.checked_at + CHECK_INTERVAL


def available_update(current: Version | None, check: ReleaseCheck | None) -> Release | None:
    """Return the latest release if it is newer than the running release build."""
    if current is None or check is None or check.latest is None:
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
