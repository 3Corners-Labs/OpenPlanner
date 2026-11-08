"""The health of the Webex and contact-center connections: the model (ADR 0082).

The services that evaluate and reconcile live in ``app.domain.integration_health``; this module
holds the values and pure rules, so the ``Transaction`` port can name them.

Three things keep an administrator informed between bookings (ADR 0082 points 5 to 7):

- **Last inbound event per platform.** Every verified webhook notes its time, event type and
  outcome (``InboundEvent``); *Koppelingen* and *System status* show when the last one came.
- **Reconcile.** Every ``task_reconcile_interval_minutes`` the worker asks the contact center
  for the state of each task that is still *queued* or *connected* and has not changed for
  ``task_reconcile_after_minutes`` past its start (``TaskReconciler``). A difference is written
  on the timeline with detail ``reconciled``; finding one writes ``integration.webhooks_missing``,
  so a silent ``inactive`` subscription becomes visible.
- **Health state** per platform and organisation, derived from the ``integration.request``
  events of the last ``integration_health_window_minutes`` (``classify``): ``ok``, ``degraded``,
  ``down``, or ``unknown`` below ``MIN_REQUESTS``. A failed token refresh (the Service App is
  shared by every organisation) or a failed probe of the group check makes a platform ``down``.
  A transition writes ``integration.health``; the return to ``ok`` waits for
  ``integration_recovery_minutes`` without a failure (``settle``).

    integration.request events ─► samples per (organisation, platform) ─► classify ─► settle
                                                     previous state ──────────────────┘
                                                        ▼ changed
                                        integration.health (failure: degraded or down,
                                                            success: ok) ─► alert (ADR 0061)

Everything here holds codes and counts: never a participant's data.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.calls import EventKind, TaskState
from app.domain.contact_center import ContactCenterTaskRef

HEALTH_EVENT = "integration.health"
"""System event of a health transition: a failure for degraded or down, a success for ok."""
WEBHOOKS_MISSING = "integration.webhooks_missing"
"""System event of a reconcile that found states the webhooks should have brought."""
MISSED = "missed"
"""Reason of a ``integration.webhooks_missing`` failure."""
RECONCILED = "reconciled"
"""Detail of a timeline event the reconcile wrote."""
MIN_REQUESTS = 3
"""Requests a window needs before it counts; below it a single failure would flip the state.
Fixed on purpose (ADR 0082, *Fixed on purpose*)."""
RECONCILE_LIMIT = 50
"""Tasks one reconcile run reads at most, the longest unchanged first; the rest wait for the
next run, so an outage cannot make the worker hammer the contact center. A fixed protection."""
RECHECK_RUNS = 6
"""A task read once is read again only after this many ``task_reconcile_after_minutes``; the
stale list is read this many times ``RECONCILE_LIMIT`` deep, so tasks that stay as they are
cannot keep newer ones out. A fixed protection, like the limit."""
WATCHED_TASKS = frozenset({TaskState.QUEUED, TaskState.CONNECTED})
"""Task states a webhook should still move on; only these are reconciled."""
TASK_EVENTS: Mapping[TaskState, EventKind] = {
    TaskState.CONNECTED: EventKind.TASK_CONNECTED,
    TaskState.ENDED: EventKind.TASK_ENDED,
    TaskState.FAILED: EventKind.TASK_FAILED,
}
"""The timeline event that brings OpenPlanner to a state the contact center reports."""


class Platform(StrEnum):
    """A connection whose health is watched."""

    WEBEX = "webex"
    WXCC = "wxcc"


class HealthState(StrEnum):
    """How a connection is doing; ``unknown`` below ``MIN_REQUESTS``."""

    OK = "ok"
    UNKNOWN = "unknown"
    DEGRADED = "degraded"
    DOWN = "down"

    @property
    def failing(self) -> bool:
        """Whether the state is a problem (an alert, ADR 0061)."""
        return self in (HealthState.DEGRADED, HealthState.DOWN)

    @property
    def severity(self) -> int:
        """Position from best (``ok``) to worst (``down``), to show the worst of several."""
        return tuple(HealthState).index(self)


@dataclass(frozen=True)
class InboundEvent:
    """The latest verified webhook of a platform: when, which event, what came of it."""

    platform: Platform
    at: datetime
    kind: str
    outcome: str


@dataclass(frozen=True)
class RequestSample:
    """One ``integration.request`` event reduced to what the health needs."""

    organisation: str | None
    platform: Platform
    failed: bool
    at: datetime


@dataclass(frozen=True)
class HealthPolicy:
    """The thresholds of the health state: the class C settings of ADR 0082.

    Built by ``Settings.health_policy``, which holds the defaults, the bounds and the rule that
    the down share is at least the degraded share.
    """

    window: timedelta
    degraded_percent: int
    down_percent: int
    recovery: timedelta


@dataclass(frozen=True)
class HealthEntry:
    """The health of one platform for one organisation (``None``: requests of no organisation).

    Attributes:
        since: When the connection entered this state.
        requests: Requests in the window of the last evaluation.
        failures: Of those, how many failed.
        reason: Why the state is what it is (``failures``, ``token_refresh``, ``probe``,
            ``recovering``, ``recovered``, ``few_requests``).
    """

    organisation: str | None
    platform: Platform
    state: HealthState
    since: datetime
    requests: int = 0
    failures: int = 0
    reason: str = ""

    @property
    def key(self) -> tuple[str, str]:
        """Which connection this is."""
        return (self.organisation or "", self.platform.value)


def classify(
    samples: Sequence[RequestSample],
    policy: HealthPolicy,
    *,
    token_failed: bool = False,
    probe_failed: bool = False,
) -> tuple[HealthState, str]:
    """Return the state the samples of one connection's window show, and why.

    A failed token refresh or probe is ``down`` whatever the requests say; fewer than
    ``MIN_REQUESTS`` requests are ``unknown``.
    """
    if token_failed:
        return HealthState.DOWN, "token_refresh"
    if probe_failed:
        return HealthState.DOWN, "probe"
    if len(samples) < MIN_REQUESTS:
        return HealthState.UNKNOWN, "few_requests"
    percent = 100 * sum(s.failed for s in samples) / len(samples)
    if percent > policy.down_percent:
        return HealthState.DOWN, "failures"
    if percent > policy.degraded_percent:
        return HealthState.DEGRADED, "failures"
    return HealthState.OK, "failures"


def settle(
    previous: HealthEntry | None,
    candidate: HealthState,
    reason: str,
    samples: Sequence[RequestSample],
    now: datetime,
    policy: HealthPolicy,
) -> tuple[HealthState, str]:
    """Return the state to keep: a failing connection returns to ``ok`` only after recovery.

    After ``degraded`` or ``down``, a healthy window (or one too quiet to tell) needs
    ``policy.recovery`` in that state without a failed request and with at least one healthy
    one; until then the failing state stays, with reason ``recovering``.
    """
    if previous is None or not previous.state.failing or candidate.failing:
        return candidate, reason
    recent = [s for s in samples if s.at >= now - policy.recovery]
    long_enough = now - previous.since >= policy.recovery
    if long_enough and recent and not any(s.failed for s in recent):
        return HealthState.OK, "recovered"
    return previous.state, "recovering"


def evaluate(
    samples: Iterable[RequestSample],
    previous: Iterable[HealthEntry],
    now: datetime,
    policy: HealthPolicy,
    *,
    token_failed: bool = False,
    probe_failed: frozenset[str] = frozenset(),
) -> tuple[tuple[HealthEntry, ...], tuple[tuple[HealthEntry | None, HealthEntry], ...]]:
    """Return every connection's health now and the transitions since ``previous``.

    Args:
        samples: The request events of the window (all organisations).
        previous: The states of the last evaluation.
        now: The current moment.
        policy: The thresholds.
        token_failed: A token refresh failed in the window (the Service App is shared).
        probe_failed: Organisations whose probe of the group check failed in the window.

    Returns:
        The entries to store, and ``(before, after)`` for each change into, out of or between
        the failing states (``_news``). A connection without requests in the window that is
        not failing is dropped, so organisations that went quiet do not pile up.
    """
    by_key: dict[tuple[str, str], list[RequestSample]] = {}
    for sample in samples:
        by_key.setdefault((sample.organisation or "", sample.platform.value), []).append(sample)
    old = {entry.key: entry for entry in previous}
    probed = {(organisation, Platform.WXCC.value) for organisation in probe_failed}
    entries: list[HealthEntry] = []
    changes: list[tuple[HealthEntry | None, HealthEntry]] = []
    for organisation, platform_value in sorted(set(by_key) | set(old) | probed):
        platform = Platform(platform_value)
        window = by_key.get((organisation, platform_value), [])
        before = old.get((organisation, platform_value))
        candidate, reason = classify(
            window,
            policy,
            token_failed=token_failed,
            probe_failed=(organisation, platform_value) in probed,
        )
        state, reason = settle(before, candidate, reason, window, now, policy)
        if not window and not state.failing:
            continue  # a connection without requests that is not failing has nothing to show
        since = before.since if before is not None and before.state is state else now
        entry = HealthEntry(
            organisation or None,
            platform,
            state,
            since,
            len(window),
            sum(s.failed for s in window),
            reason,
        )
        entries.append(entry)
        if _news(before.state if before else None, state):
            changes.append((before, entry))
    return tuple(entries), tuple(changes)


def _news(before: HealthState | None, after: HealthState) -> bool:
    """Whether a change is worth an event: into, out of, or between the failing states.

    ``ok`` and ``unknown`` say nothing is wrong; moving between them (traffic stopped or
    started) is no news, and neither is a first sighting that is not failing.
    """
    was_failing = before is not None and before.failing
    if not (was_failing or after.failing):
        return False
    return before is not after


def health_event(before: HealthEntry | None, after: HealthEntry, now: datetime) -> AuditEvent:
    """Return the ``integration.health`` event of one transition (codes and counts only).

    A failing state is a failure with the state as reason (``down`` is an outage, ADR 0061);
    the return to ``ok`` or ``unknown`` closes the alert as a success.
    """
    old = before.state.value if before else "none"
    return AuditEvent(
        now,
        SYSTEM_ACTOR,
        HEALTH_EVENT,
        Outcome.FAILURE if after.state.failing else Outcome.SUCCESS,
        after.state.value if after.state.failing else "",
        after.organisation,
        None,
        f"platform:{after.platform.value},from:{old},to:{after.state.value},"
        f"requests:{after.requests},failures:{after.failures},why:{after.reason}",
        Source.SYSTEM,
    )


@dataclass(frozen=True)
class StaleTask:
    """A booking whose task a webhook should have moved by now (no personal data).

    ``task`` names the platform that holds it (ADR 0032); that platform's dispatcher reads it.
    """

    booking_id: int
    task: ContactCenterTaskRef
    state: TaskState
    organisation: str | None
    group: str | None = None
