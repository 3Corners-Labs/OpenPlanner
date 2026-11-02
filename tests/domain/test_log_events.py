"""The register of actions is complete and consistent (ADR 0103 point 1)."""

from collections import Counter

import pytest

from app.domain.authorization import ApiScope, Permission
from app.domain.job_plan import JobType
from app.domain.log_events import (
    BY_OUTCOME,
    COMMON_FIELDS,
    ENTRIES,
    EVENTS,
    LEVELS,
    LogCategory,
    LogEvent,
    all_events,
    lookup,
    trace_points,
)


def test_every_action_is_listed_once() -> None:
    twice = [action for action, n in Counter(e.action for e in ENTRIES).items() if n > 1]

    assert twice == []


@pytest.mark.parametrize("entry", list(all_events()), ids=lambda e: e.action)
def test_an_entry_is_complete(entry: LogEvent) -> None:
    assert entry.levels, "an entry needs a level"
    assert set(entry.levels) <= {*LEVELS, BY_OUTCOME}
    assert entry.message.endswith("."), "the message is a sentence"
    assert entry.meaning
    assert entry.todo
    assert entry.placeholders <= {*entry.fields, *COMMON_FIELDS}, (
        f"{entry.action}: the message names a field the entry does not declare"
    )


def test_a_trace_point_says_what_it_never_carries() -> None:
    points = [e for e in ENTRIES if e.is_trace_point]

    assert points
    assert all(e.never for e in points)
    assert trace_points() == {e.action: e.fields for e in points}


def test_permissions_scopes_and_jobs_are_generated() -> None:
    permission = lookup(Permission.GROUP_EDIT.value)
    scope = lookup(ApiScope.AVAILABILITY_READ.value)
    job = lookup(f"job.{JobType.SEND_REMINDER.value}")

    assert permission is not None
    assert permission.category is LogCategory.AUDIT
    assert scope is not None
    assert scope.category is LogCategory.AUDIT
    assert job is not None
    assert job.category is LogCategory.SYSTEM
    assert job.levels == (BY_OUTCOME,)


def test_a_listed_entry_wins_over_a_generated_one() -> None:
    assert lookup("job.run") is EVENTS["job.run"]


def test_an_unknown_action_is_not_registered() -> None:
    assert lookup("no.such_action") is None


def test_every_action_appears_once_in_the_full_list() -> None:
    actions = [e.action for e in all_events()]

    assert len(actions) == len(set(actions))
