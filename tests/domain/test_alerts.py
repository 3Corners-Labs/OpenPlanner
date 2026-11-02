"""Alerts (ADR 0061 §7): open streaks of condition events, levels, reach, banner and pop-ups."""

from datetime import timedelta

import pytest

from app.domain.alerts import (
    ALERT_RULES,
    MAX_DISMISSED,
    QUEUE_WAIT_EVENT,
    WORKER_ALERT,
    AlertLevel,
    AlertService,
    at_least,
    banner_alerts,
    lowest_popup,
    open_alerts,
    open_conditions,
    remember_dismissal,
    worker_alert,
)
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.authorization import Role
from app.domain.integration_check import CHECK_EVENT
from app.domain.log_destinations import HEALTH_EVENT
from app.domain.storage import STORAGE_EVENT
from app.domain.tls import EVENT as CERTIFICATE_EVENT
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import DEMO_ORG_ADMIN, DEMO_SUPERUSER, NOW
from tests.support.principals import principal

MINUTE = timedelta(minutes=1)


def event(
    action: str,
    outcome: Outcome,
    minutes: int,
    reason: str = "",
    *,
    organisation: str | None = None,
    group: str | None = None,
    detail: str = "",
    source: Source = Source.SYSTEM,
) -> AuditEvent:
    return AuditEvent(
        NOW + minutes * MINUTE,
        SYSTEM_ACTOR,
        action,
        outcome,
        reason,
        organisation,
        group,
        detail,
        source,
    )


def test_a_failure_opens_an_alert_and_a_success_ends_it() -> None:
    events = [
        event(STORAGE_EVENT, Outcome.FAILURE, 0, "free_space_low"),
        event(STORAGE_EVENT, Outcome.SUCCESS, 5),
    ]

    assert open_alerts(events) == []
    assert len(open_alerts(events[:1])) == 1


def test_the_streak_starts_at_its_first_failure_and_takes_the_latest_level() -> None:
    events = [
        event(STORAGE_EVENT, Outcome.FAILURE, 0, "free_space_low"),
        event(STORAGE_EVENT, Outcome.SUCCESS, 1),
        event(STORAGE_EVENT, Outcome.FAILURE, 2, "free_space_low"),
        event(STORAGE_EVENT, Outcome.FAILURE, 3, "free_space_critical", detail="free_percent:4"),
    ]

    [alert] = open_alerts(reversed(events))  # the order of reading does not matter

    assert (alert.since, alert.latest) == (NOW + 2 * MINUTE, NOW + 3 * MINUTE)
    assert (alert.level, alert.reason, alert.detail) == (
        AlertLevel.OUTAGE,
        "free_space_critical",
        "free_percent:4",
    )


def test_retries_skips_and_other_actions_change_nothing() -> None:
    events = [
        event(STORAGE_EVENT, Outcome.FAILURE, 0, "free_space_low"),
        event(STORAGE_EVENT, Outcome.RETRY, 1),
        event(STORAGE_EVENT, Outcome.SKIPPED, 2),
        event("job.send_reminder", Outcome.FAILURE, 3),
    ]

    assert [a.action for a in open_alerts(events)] == [STORAGE_EVENT]


def test_each_group_and_subject_is_its_own_condition() -> None:
    events = [
        event(CHECK_EVENT, Outcome.FAILURE, 0, "webex.host_unknown", organisation="o", group="a"),
        event(CHECK_EVENT, Outcome.FAILURE, 1, "webex.host_unknown", organisation="o", group="b"),
        event(CHECK_EVENT, Outcome.SUCCESS, 2, organisation="o", group="a"),
        event(HEALTH_EVENT, Outcome.FAILURE, 3, "timeout", detail="kind:syslog,destination:1"),
        event(HEALTH_EVENT, Outcome.FAILURE, 4, "timeout", detail="kind:syslog,destination:2"),
        event(HEALTH_EVENT, Outcome.SUCCESS, 5, detail="change:recovered,destination:1"),
    ]

    found = {(a.action, a.group, a.subject) for a in open_alerts(events)}

    assert found == {(CHECK_EVENT, "b", ""), (HEALTH_EVENT, None, "2")}


@pytest.mark.parametrize(
    ("action", "reason", "level"),
    [
        (STORAGE_EVENT, "free_space_low", AlertLevel.WARNING),
        (STORAGE_EVENT, "database_too_large", AlertLevel.WARNING),
        (STORAGE_EVENT, "free_space_critical", AlertLevel.OUTAGE),
        (CERTIFICATE_EVENT, "expiring", AlertLevel.WARNING),
        (CERTIFICATE_EVENT, "expired", AlertLevel.OUTAGE),
        (CERTIFICATE_EVENT, "untrusted", AlertLevel.OUTAGE),
        (QUEUE_WAIT_EVENT, "wait_over_warning", AlertLevel.WARNING),
    ],
)
def test_the_reason_decides_the_level(action: str, reason: str, level: AlertLevel) -> None:
    [alert] = open_alerts([event(action, Outcome.FAILURE, 0, reason)])

    assert alert.level is level


def test_outages_come_first_then_the_most_recent() -> None:
    events = [
        event(CERTIFICATE_EVENT, Outcome.FAILURE, 0, "expiring"),
        event(STORAGE_EVENT, Outcome.FAILURE, 1, "free_space_critical"),
        event(
            QUEUE_WAIT_EVENT, Outcome.FAILURE, 2, "wait_over_warning", organisation="o", group="a"
        ),
    ]

    assert [a.action for a in open_alerts(events)] == [
        STORAGE_EVENT,
        QUEUE_WAIT_EVENT,
        CERTIFICATE_EVENT,
    ]


def test_every_rule_names_a_distinct_action() -> None:
    actions = [rule.action for rule in ALERT_RULES]

    assert len(actions) == len(set(actions))


def test_the_token_changes_with_a_new_streak_or_a_higher_level() -> None:
    [low] = open_alerts([event(STORAGE_EVENT, Outcome.FAILURE, 0, "free_space_low")])
    [full] = open_alerts(
        [
            event(STORAGE_EVENT, Outcome.FAILURE, 0, "free_space_low"),
            event(STORAGE_EVENT, Outcome.FAILURE, 1, "free_space_critical"),
        ]
    )
    [again] = open_alerts([event(STORAGE_EVENT, Outcome.FAILURE, 9, "free_space_low")])

    assert len({low.token, full.token, again.token}) == 3
    assert (
        low.token
        == open_alerts([event(STORAGE_EVENT, Outcome.FAILURE, 0, "free_space_low")])[0].token
    )


def test_the_banner_shows_open_outages_that_were_not_closed() -> None:
    alerts = open_alerts(
        [
            event(STORAGE_EVENT, Outcome.FAILURE, 0, "free_space_critical"),
            event(CERTIFICATE_EVENT, Outcome.FAILURE, 0, "expired"),
            event(QUEUE_WAIT_EVENT, Outcome.FAILURE, 0, "wait_over_warning", group="a"),
        ]
    )
    storage = next(a for a in alerts if a.action == STORAGE_EVENT)

    assert {a.action for a in banner_alerts(alerts, [])} == {STORAGE_EVENT, CERTIFICATE_EVENT}
    assert [a.action for a in banner_alerts(alerts, [storage.token])] == [CERTIFICATE_EVENT]


def test_closed_alerts_are_remembered_up_to_a_maximum() -> None:
    kept: list[str] = []
    for number in range(MAX_DISMISSED + 3):
        kept = remember_dismissal(kept, f"{number:016x}")
    kept = remember_dismissal(kept, kept[0])

    assert len(kept) == MAX_DISMISSED
    assert kept[-1] == f"{3:016x}"


@pytest.mark.parametrize(
    ("choice", "floor"),
    [
        ("all", AlertLevel.INFO),
        ("warnings", AlertLevel.WARNING),
        ("outages", AlertLevel.OUTAGE),
        ("off", None),
        ("nonsense", AlertLevel.WARNING),
    ],
)
def test_the_popup_choice_sets_the_least_urgent_level(
    choice: str, floor: AlertLevel | None
) -> None:
    assert lowest_popup(choice) is floor


def test_the_level_filter_keeps_that_level_and_above() -> None:
    alerts = open_alerts(
        [
            event(STORAGE_EVENT, Outcome.FAILURE, 0, "free_space_critical"),
            event(CERTIFICATE_EVENT, Outcome.FAILURE, 0, "expiring"),
        ]
    )

    assert len(at_least(alerts, AlertLevel.INFO)) == 2
    assert [a.action for a in at_least(alerts, AlertLevel.OUTAGE)] == [STORAGE_EVENT]
    assert at_least(alerts, None) == []


def test_a_stale_heartbeat_is_an_outage_and_none_is_no_alert() -> None:
    limit = timedelta(minutes=5)

    assert worker_alert(None, NOW, limit) is None
    assert worker_alert(NOW - limit, NOW, limit) is None
    stale = worker_alert(NOW - limit - MINUTE, NOW, limit)
    assert stale is not None
    assert (stale.action, stale.level) == (WORKER_ALERT, AlertLevel.OUTAGE)


def service(state: State, beat_minutes_ago: int | None = None) -> AlertService:
    beat = None if beat_minutes_ago is None else NOW - beat_minutes_ago * MINUTE
    return AlertService(
        InMemoryUnitOfWork(state), FixedClock(NOW), lambda: beat, lambda: timedelta(minutes=5)
    )


async def test_platform_alerts_reach_only_who_may_see_the_platform() -> None:
    state = State()
    state.audit.append(event(STORAGE_EVENT, Outcome.FAILURE, 0, "free_space_critical"))
    state.audit.append(
        event(
            QUEUE_WAIT_EVENT,
            Outcome.FAILURE,
            0,
            "wait_over_warning",
            organisation="demo",
            group="a",
        )
    )
    state.audit.append(
        event(
            QUEUE_WAIT_EVENT,
            Outcome.FAILURE,
            0,
            "wait_over_warning",
            organisation="other",
            group="b",
        )
    )
    alerts = service(state, beat_minutes_ago=30)

    superuser = {(a.action, a.group) for a in await alerts.open(DEMO_SUPERUSER)}
    org_admin = {(a.action, a.group) for a in await alerts.open(DEMO_ORG_ADMIN)}

    assert superuser == {
        (STORAGE_EVENT, None),
        (WORKER_ALERT, None),
        (QUEUE_WAIT_EVENT, "a"),
        (QUEUE_WAIT_EVENT, "b"),
    }
    assert org_admin == {(QUEUE_WAIT_EVENT, "a")}


async def test_an_agent_sees_the_wait_alerts_of_their_own_groups() -> None:
    state = State()
    state.audit.append(
        event(
            QUEUE_WAIT_EVENT,
            Outcome.FAILURE,
            0,
            "wait_over_warning",
            organisation="demo",
            group="a",
        )
    )
    state.audit.append(
        event(CHECK_EVENT, Outcome.FAILURE, 0, "webex.host_unknown", organisation="demo", group="a")
    )
    state.audit.append(
        event(
            QUEUE_WAIT_EVENT,
            Outcome.FAILURE,
            0,
            "wait_over_warning",
            organisation="demo",
            group="z",
        )
    )
    agent = principal("a-1", "A", {Role.AGENT}, groups=[("demo", "a")])

    found = await service(state).open(agent)

    assert [(a.action, a.group) for a in found] == [(QUEUE_WAIT_EVENT, "a")]


async def test_an_admin_action_with_the_same_key_is_no_condition() -> None:
    state = State()
    state.audit.append(
        event(
            CHECK_EVENT,
            Outcome.FAILURE,
            0,
            "fail",
            organisation="demo",
            group="a",
            source=Source.ADMIN,
        )
    )

    assert await service(state).open(DEMO_SUPERUSER) == []


async def test_a_watch_reads_the_open_conditions_of_its_action() -> None:
    state = State()
    state.audit.append(event(STORAGE_EVENT, Outcome.FAILURE, 0, "free_space_low"))
    state.audit.append(
        event(
            QUEUE_WAIT_EVENT,
            Outcome.FAILURE,
            0,
            "wait_over_warning",
            organisation="demo",
            group="a",
        )
    )

    found = await open_conditions(InMemoryUnitOfWork(state), QUEUE_WAIT_EVENT)

    assert [(a.action, a.group) for a in found] == [(QUEUE_WAIT_EVENT, "a")]
