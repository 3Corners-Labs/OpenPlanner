"""When System status warns about an old update back-up (ADR 0102 point 10)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.backup_age import (
    BACKUP_GRACE,
    BACKUP_LIFETIME,
    BackupAge,
    BackupGrade,
    backup_grade,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def test_the_lifetime_is_a_day_and_the_grace_is_short() -> None:
    assert BACKUP_LIFETIME == timedelta(hours=24)
    assert timedelta(0) < BACKUP_GRACE <= timedelta(hours=6)


def test_without_a_backup_there_is_nothing_to_warn_about() -> None:
    assert backup_grade(None, NOW) is BackupGrade.NONE


@pytest.mark.parametrize(
    ("age", "grade"),
    [
        (timedelta(minutes=5), BackupGrade.OK),
        (BACKUP_LIFETIME, BackupGrade.OK),
        (BACKUP_LIFETIME + BACKUP_GRACE, BackupGrade.OK),
        (BACKUP_LIFETIME + BACKUP_GRACE + timedelta(seconds=1), BackupGrade.OVERDUE),
        (timedelta(days=9), BackupGrade.OVERDUE),
    ],
)
def test_a_backup_past_its_lifetime_plus_grace_is_overdue(
    age: timedelta, grade: BackupGrade
) -> None:
    assert backup_grade(BackupAge(2, NOW - age), NOW) is grade
