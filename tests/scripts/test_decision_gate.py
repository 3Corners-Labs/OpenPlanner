"""Build work passes the decision gate, a decision waits for the label (ADR 0078).

The gate judges a pull request, and again every pull request in a merge group by its own
commit on the queue branch (ADR 0088), so a decision cannot reach ``main`` without the label.
"""

import re
from pathlib import Path

import pytest

from scripts.decision_gate import (
    LABEL,
    adr_changed_decision,
    decision_part,
    main,
    parse_pull,
    pull_request_number,
    reasons,
)
from tests.scripts.conftest import Git, commit_all

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "decision-gate.yml"

ADR = """# 0099. An example

Status: proposed (2026-10-31), requested by the product owner.
Partly built; see the table.

## Context

Why.

## Decision

1. The rule.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #1 | in PR |

## Consequences

What follows.
"""


def test_the_decision_part_leaves_out_the_status_line_and_the_build_table() -> None:
    part = decision_part(ADR)

    assert "Status:" not in part
    assert "Partly built" not in part
    assert "| 0 | ADR proposed" not in part
    assert "1. The rule." in part
    assert "What follows." in part


def test_a_build_pr_may_change_the_status_line_and_the_build_table() -> None:
    built = ADR.replace("Partly built; see the table.", "Built.").replace(
        "| 0 | ADR proposed | #1 | in PR |",
        "| 0 | ADR proposed | #1 | merged |\n| 1 | x | #2 | merged |",
    )

    assert adr_changed_decision(ADR, built) is False


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (ADR, ADR.replace("1. The rule.", "1. Another rule.")),
        (ADR, ADR.replace("Why.", "Why not.")),
        (None, ADR),
        (ADR, None),
    ],
)
def test_a_change_to_the_decision_itself_or_a_new_or_removed_adr_is_a_decision(
    before: str | None, after: str | None
) -> None:
    assert adr_changed_decision(before, after) is True


def test_build_work_has_no_reasons() -> None:
    paths = ["app/domain/booking.py", "tests/domain/test_booking.py", "docs/adr-status.md"]

    assert reasons(paths, "feat(domain): x", lambda _: (None, None)) == []


def test_every_decision_path_and_the_release_title_are_named() -> None:
    paths = [
        "CLAUDE.md",
        ".github/workflows/ci.yml",
        ".semgrep/no-print.yml",
        "Makefile",
        "app/domain/authorization.py",
        "scripts/check-release-audit.sh",
        "docs/adr/0099-example.md",
        "app/domain/booking.py",
        "pyproject.toml",
    ]
    texts = {"docs/adr/0099-example.md": (ADR, ADR.replace("1. The rule.", "2."))}

    found = reasons(paths, "chore(release): v0.4.0", lambda p: texts.get(p, (None, None)))

    assert found == [
        "the title says it is a release",
        ".github/workflows/ci.yml changed",
        ".semgrep/no-print.yml changed",
        "CLAUDE.md changed",
        "Makefile changed",
        "app/domain/authorization.py changed",
        "docs/adr/0099-example.md changed outside its Status line and Build status table",
        "scripts/check-release-audit.sh changed",
    ]


@pytest.fixture
def adr(git: Git, tmp_path: Path) -> Path:
    """The repository holds one ADR and one source file on ``main``; work happens on ``work``."""
    path = tmp_path / "docs" / "adr" / "0099-example.md"
    path.parent.mkdir(parents=True)
    path.write_text(ADR)
    (tmp_path / "app.py").write_text("x = 1\n")
    commit_all(git, "base")
    git("switch", "-q", "-c", "work")
    return path


def test_the_gate_passes_build_work_in_a_real_repository(
    git: Git, adr: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (adr.parents[2] / "app.py").write_text("x = 2\n")
    adr.write_text(ADR.replace("| in PR |", "| merged |"))
    commit_all(git, "feat: build")

    assert main(["--base", "main", "--head", "work", "--title", "feat: build"]) == 0
    assert "build work" in capsys.readouterr().out


def test_the_gate_stops_a_decision_until_the_label_is_set(
    git: Git, adr: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    adr.write_text(ADR.replace("1. The rule.", "1. A new rule."))
    commit_all(git, "docs(adr): change")
    arguments = ["--base", "main", "--head", "work", "--title", "docs(adr): change"]

    assert main(arguments) == 1
    out = capsys.readouterr().out
    assert "docs/adr/0099-example.md changed outside" in out
    assert "::error::" in out

    assert main([*arguments, "--labels", f"ci:full,{LABEL}"]) == 0
    assert "approved" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("subject", "number"),
    [
        ("feat(logging): the TRACE level (ADR 0085 step 1) (#266)", 266),
        ("Merge pull request #263 from 3Corners-Labs/logging-syslog", 263),
        ("docs: a change (#12)\n", 12),
        ("feat: something without a number", None),
        ("fix: issue #12 mentioned in passing", None),
    ],
)
def test_the_pull_request_number_comes_from_the_queue_commits_subject(
    subject: str, number: int | None
) -> None:
    assert pull_request_number(subject) == number


def test_a_pull_argument_carries_the_number_and_its_labels() -> None:
    assert parse_pull(f"266=ci:full, {LABEL}") == (266, {"ci:full", LABEL})
    assert parse_pull("267=") == (267, set())


@pytest.fixture
def queue(git: Git, adr: Path) -> Path:
    """The merge queue branch ``queue``: ``main`` plus one squash commit per pull request.

    Pull request 5 is build work; pull request 6 adds an ADR (a decision) without the label.
    """
    git("switch", "-q", "-c", "queue", "main")
    (adr.parents[2] / "app.py").write_text("x = 2\n")
    commit_all(git, "feat: build (#5)")
    new = adr.with_name("0100-new.md")
    new.write_text(ADR.replace("0099", "0100"))
    commit_all(git, "docs(adr): a new decision (#6)")
    return new


def test_the_gate_lists_the_pull_requests_in_the_merge_group(
    git: Git, queue: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--base", "main", "--head", "queue", "--list-pull-requests"]) == 0
    assert capsys.readouterr().out.split() == ["5", "6"]


def test_a_merge_group_with_a_new_adr_fails_without_the_label(
    git: Git, queue: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = ["--base", "main", "--head", "queue", "--merge-group", "--pull", "5="]

    assert main([*arguments, "--pull", "6=ci:full"]) == 1
    out = capsys.readouterr().out
    assert "build work" in out
    assert "docs/adr/0100-new.md changed outside" in out
    assert f"::error::a decision waits for the product owner: the label {LABEL} is missing" in out

    assert main([*arguments, "--pull", f"6={LABEL}"]) == 0
    assert "approved" in capsys.readouterr().out


def test_the_label_on_one_pull_request_does_not_cover_another_in_the_group(
    git: Git, queue: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each pull request is judged by its own commit, so the group's labels never pool."""
    arguments = ["--base", "main", "--head", "queue", "--merge-group"]

    assert main([*arguments, "--pull", f"5={LABEL}", "--pull", "6="]) == 1
    assert "::error::" in capsys.readouterr().out


def test_the_gate_fails_closed_when_a_pull_requests_labels_are_missing(
    git: Git, queue: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--base", "main", "--head", "queue", "--merge-group", "--pull", "5="]) == 1
    assert "labels of pull request #6 were not given" in capsys.readouterr().out


def test_the_gate_fails_closed_on_a_queue_commit_that_names_no_pull_request(
    git: Git, queue: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (queue.parents[2] / "app.py").write_text("x = 3\n")
    commit_all(git, "feat: a commit from nowhere")

    assert main(["--base", "main", "--head", "queue", "--list-pull-requests"]) == 1
    assert "names no pull request" in capsys.readouterr().out
    assert main(["--base", "main", "--head", "queue", "--merge-group", "--pull", "5="]) == 1


def test_a_merge_group_of_build_work_passes_without_labels(
    git: Git, adr: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git("switch", "-q", "-c", "queue", "main")
    (adr.parents[2] / "app.py").write_text("x = 2\n")
    commit_all(git, "feat: build (#5)")

    assert main(["--base", "main", "--head", "queue", "--merge-group", "--pull", "5="]) == 0
    assert "build work" in capsys.readouterr().out


def test_the_workflow_runs_the_gate_in_the_merge_queue_too() -> None:
    """A skipped required check counts as passed; the job must run on ``merge_group``."""
    text = WORKFLOW.read_text()

    assert re.search(r"^  merge_group:", text, re.M)
    assert re.search(r"^  pull_request_target:", text, re.M)
    assert "edited" in text, "a change of the base branch changes the diff; rerun on edited"
    assert "\n    if: github.event_name == 'pull_request_target'" not in text, "job-level skip"
    assert "--merge-group" in text
    assert "--list-pull-requests" in text
    assert "github.event.merge_group.base_sha" in text
    assert "github.event.merge_group.head_sha" in text
    assert "pull-requests: read" in text
