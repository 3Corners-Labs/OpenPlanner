"""The health model of the connections (ADR 0082 point 7): classify, settle, transitions."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.audit import Outcome
from app.domain.connection_health import (
    MIN_REQUESTS,
    HealthEntry,
    HealthPolicy,
    HealthState,
    Platform,
    RequestSample,
    classify,
    evaluate,
    health_event,
    settle,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
POLICY = HealthPolicy(timedelta(minutes=15), 25, 75, timedelta(minutes=10))


def samples(
    ok: int, failed: int, org: str | None = "demo", minutes_ago: int = 1
) -> list[RequestSample]:
    at = NOW - timedelta(minutes=minutes_ago)
    return [RequestSample(org, Platform.WXCC, False, at) for _ in range(ok)] + [
        RequestSample(org, Platform.WXCC, True, at) for _ in range(failed)
    ]


@pytest.mark.parametrize(
    ("ok", "failed", "state"),
    [
        (10, 0, HealthState.OK),
        (7, 3, HealthState.DEGRADED),
        (2, 8, HealthState.DOWN),
        (3, 1, HealthState.OK),
        (1, 1, HealthState.UNKNOWN),
    ],
)
def test_the_share_of_failures_decides_the_state(ok: int, failed: int, state: HealthState) -> None:
    assert classify(samples(ok, failed), POLICY)[0] is state


def test_fewer_requests_than_the_floor_are_unknown() -> None:
    assert classify(samples(0, MIN_REQUESTS - 1), POLICY) == (HealthState.UNKNOWN, "few_requests")


def test_a_failed_token_refresh_or_probe_is_down_whatever_the_requests() -> None:
    assert classify(samples(10, 0), POLICY, token_failed=True) == (
        HealthState.DOWN,
        "token_refresh",
    )
    assert classify([], POLICY, probe_failed=True) == (HealthState.DOWN, "probe")


def test_a_failing_connection_returns_to_ok_only_after_the_recovery_time() -> None:
    down = HealthEntry("demo", Platform.WXCC, HealthState.DOWN, NOW - timedelta(minutes=5))
    healthy = samples(5, 0)
    assert settle(down, HealthState.OK, "failures", healthy, NOW, POLICY) == (
        HealthState.DOWN,
        "recovering",
    )
    long_ago = HealthEntry("demo", Platform.WXCC, HealthState.DOWN, NOW - timedelta(minutes=30))
    assert settle(long_ago, HealthState.OK, "failures", healthy, NOW, POLICY)[0] is HealthState.OK
    # A recent failure keeps it failing; so does a window too quiet to show health.
    assert settle(long_ago, HealthState.OK, "", samples(5, 1), NOW, POLICY)[0] is HealthState.DOWN
    assert settle(long_ago, HealthState.UNKNOWN, "", [], NOW, POLICY)[0] is HealthState.DOWN
    # A worse candidate applies at once.
    degraded = HealthEntry("demo", Platform.WXCC, HealthState.DEGRADED, NOW)
    assert settle(degraded, HealthState.DOWN, "failures", [], NOW, POLICY)[0] is HealthState.DOWN


def test_ok_and_unknown_are_no_news_and_quiet_connections_are_dropped() -> None:
    ok = HealthEntry("demo", Platform.WXCC, HealthState.OK, NOW)
    entries, changes = evaluate(samples(1, 0), (ok,), NOW, POLICY)
    assert [e.state for e in entries] == [HealthState.UNKNOWN]
    assert changes == ()
    entries, changes = evaluate([], entries, NOW, POLICY)
    assert (entries, changes) == ((), ())


def test_transitions_are_reported_and_a_healthy_first_sighting_is_not() -> None:
    entries, changes = evaluate(samples(10, 0), (), NOW, POLICY)
    assert [e.state for e in entries] == [HealthState.OK]
    assert changes == ()
    entries, changes = evaluate(samples(1, 9), entries, NOW, POLICY)
    assert [(b.state if b else None, a.state) for b, a in changes] == [
        (HealthState.OK, HealthState.DOWN)
    ]
    later = NOW + timedelta(minutes=20)
    healed = [RequestSample("demo", Platform.WXCC, False, later - timedelta(minutes=1))] * 4
    entries, changes = evaluate(healed, entries, later, POLICY)
    assert [a.state for _, a in changes] == [HealthState.OK]
    assert entries[0].since == later


def test_each_organisation_and_platform_has_its_own_state() -> None:
    mixed = [
        *samples(10, 0, "demo"),
        *samples(0, 10, "andere"),
        RequestSample("demo", Platform.WEBEX, True, NOW),
    ]
    entries, changes = evaluate(mixed, (), NOW, POLICY, probe_failed=frozenset({"derde"}))
    states = {(e.organisation, e.platform): e.state for e in entries}
    assert states[("demo", Platform.WXCC)] is HealthState.OK
    assert states[("andere", Platform.WXCC)] is HealthState.DOWN
    assert states[("demo", Platform.WEBEX)] is HealthState.UNKNOWN
    assert states[("derde", Platform.WXCC)] is HealthState.DOWN
    assert {a.organisation for _, a in changes} == {"andere", "derde"}


def test_the_event_carries_codes_and_counts_only() -> None:
    after = HealthEntry("demo", Platform.WXCC, HealthState.DOWN, NOW, 10, 9, "failures")
    event = health_event(None, after, NOW)
    assert (event.outcome, event.reason, event.organisation) == (Outcome.FAILURE, "down", "demo")
    assert event.detail == ("platform:wxcc,from:none,to:down,requests:10,failures:9,why:failures")
    back = health_event(after, HealthEntry("demo", Platform.WXCC, HealthState.OK, NOW), NOW)
    assert (back.outcome, back.reason) == (Outcome.SUCCESS, "")
