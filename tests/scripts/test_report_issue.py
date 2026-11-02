"""One open report issue: created, updated in place, closed when clean (ADR 0109)."""

from collections.abc import Sequence
from datetime import date

import pytest

from scripts.report_issue import IssueTracker, keep_report

TODAY = date(2026, 11, 2)


class Fake:
    """Records every gh call and answers the reads."""

    def __init__(self, labels: str = "[]") -> None:
        self.calls: list[list[str]] = []
        self._labels = labels

    def __call__(self, args: Sequence[str]) -> str:
        self.calls.append(list(args))
        if args[:2] == ["label", "list"]:
            return self._labels
        if args[:2] == ["issue", "create"]:
            return "https://github.com/o/r/issues/77\n"
        return ""


def keep(
    fake: Fake, open_issues: list[tuple[int, str]], found: bool, dry_run: bool = False
) -> None:
    keep_report(
        IssueTracker(fake),
        open_issues,
        "Drift",
        "drift",
        "the report",
        "body",
        found,
        TODAY,
        dry_run,
    )


def test_a_finding_creates_the_issue_and_its_label() -> None:
    fake = Fake()

    keep(fake, [(3, "Something else")], found=True)

    assert [
        "label",
        "create",
        "drift",
        "--color",
        "5319e7",
        "--description",
        "the report",
    ] in fake.calls
    assert [
        "issue",
        "create",
        "--title",
        "Drift 2026-11-02",
        "--body",
        "body",
        "--label",
        "drift",
    ] in (fake.calls)


def test_an_open_report_is_updated_in_place_not_duplicated() -> None:
    fake = Fake()

    keep(fake, [(5, "Drift 2026-11-01"), (9, "Drift 2026-10-30")], found=True)

    assert fake.calls == [["issue", "edit", "5", "--title", "Drift 2026-11-02", "--body", "body"]]


def test_nothing_found_closes_the_open_report() -> None:
    fake = Fake()

    keep(fake, [(5, "Drift 2026-11-01")], found=False)

    assert fake.calls[0][:3] == ["issue", "close", "5"]


def test_nothing_found_and_no_report_does_nothing() -> None:
    fake = Fake()

    keep(fake, [], found=False)

    assert fake.calls == []


def test_a_dry_run_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    fake = Fake()

    keep(fake, [], found=True, dry_run=True)

    assert all(call[:2] == ["label", "list"] for call in fake.calls)
    assert "would create: Drift 2026-11-02" in capsys.readouterr().out


def test_open_issues_come_oldest_first() -> None:
    tracker = IssueTracker(
        lambda args: '[{"number": 9, "title": "b"}, {"number": 2, "title": "a"}]'
    )

    assert tracker.open_issues() == [(2, "a"), (9, "b")]
