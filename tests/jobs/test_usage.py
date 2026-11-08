"""The worker's usage wrappers log counts and error types and never raise (ADR 0117 point 4)."""

from structlog.testing import capture_logs

from app.domain.usage_backfill_service import BackfillRun, UsageBackfill
from app.domain.usage_rollup import UsagePruned
from app.domain.usage_rollup_service import RollupRun, UsageRollup
from app.jobs.usage import backfill_usage, roll_up_usage


class _Rollup(UsageRollup):
    def __init__(self, run: RollupRun | None = None, error: Exception | None = None) -> None:
        self._result = run
        self._error = error

    async def run_if_due(self) -> RollupRun | None:
        if self._error is not None:
            raise self._error
        return self._result


class _Backfill(UsageBackfill):
    def __init__(self, run: BackfillRun | None = None, error: Exception | None = None) -> None:
        self._result = run or BackfillRun()
        self._error = error

    async def run_once(self) -> BackfillRun:
        if self._error is not None:
            raise self._error
        return self._result


async def test_a_run_that_did_something_is_logged_with_counts_only() -> None:
    run = RollupRun(5, 9, 1, UsagePruned(2, 3))

    with capture_logs() as logs:
        assert await roll_up_usage(_Rollup(run)) == run

    assert logs == [
        {
            "event": "usage.rolled_up",
            "log_level": "info",
            "facts": 5,
            "cells": 9,
            "unreadable": 1,
            "events_pruned": 2,
            "hours_pruned": 3,
        }
    ]


async def test_a_run_with_nothing_to_do_is_silent() -> None:
    with capture_logs() as logs:
        assert await roll_up_usage(_Rollup(RollupRun())) == RollupRun()
        assert await roll_up_usage(_Rollup(None)) is None

    assert logs == []


async def test_a_failing_rollup_is_logged_with_the_error_type_and_returns_none() -> None:
    with capture_logs() as logs:
        assert await roll_up_usage(_Rollup(error=RuntimeError("secret detail"))) is None

    assert logs == [{"event": "usage.rollup_failed", "log_level": "error", "error": "RuntimeError"}]
    assert "secret detail" not in repr(logs)


async def test_the_backfill_logs_what_it_made() -> None:
    with capture_logs() as logs:
        await backfill_usage(_Backfill(BackfillRun(4, 11)))

    assert logs == [
        {
            "event": "usage.backfilled",
            "log_level": "info",
            "bookings": 4,
            "facts": 11,
            "complete": False,
        }
    ]


async def test_a_failing_backfill_never_stops_the_start() -> None:
    with capture_logs() as logs:
        await backfill_usage(_Backfill(error=ValueError("x")))

    assert logs == [
        {"event": "usage.backfill_failed", "log_level": "warning", "error": "ValueError"}
    ]
