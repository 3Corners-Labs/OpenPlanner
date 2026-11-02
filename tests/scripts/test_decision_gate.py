"""Build work passes the decision gate, a decision waits for the label (ADR 0078).

The gate judges a pull request, and again every pull request in a merge group by its own
commit on the queue branch (ADR 0088), so a decision cannot reach ``main`` without the label.
The label counts only when an administrator set it last (ADR 0104).
"""

import json
import re
from pathlib import Path

import pytest

from scripts.decision_gate import (
    LABEL,
    adr_changed_decision,
    approval,
    decision_part,
    main,
    parse_pull,
    pull_request_number,
    reasons,
)
from tests.scripts.conftest import Git, commit_all

ADMIN = "owner"
"""An administrator of the repository in these tests."""

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


def labelled(by: str, label: str = LABEL, event: str = "labeled") -> dict[str, str]:
    """One label event as the workflow writes it."""
    return {"event": event, "actor": by, "label": label}


def events_file(path: Path, *events: dict[str, str]) -> str:
    """Write label events as JSON lines, the workflow's format, and return the path."""
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    return str(path)


@pytest.fixture
def admins(tmp_path: Path) -> list[str]:
    """The ``--admins`` arguments: a file with one administrator per line."""
    path = tmp_path / "admins"
    path.write_text(f"{ADMIN}\n")
    return ["--admins", str(path)]


def test_an_administrators_label_is_an_approval() -> None:
    assert approval([labelled(ADMIN)], {ADMIN}) == (
        True,
        f"the label {LABEL} was set by {ADMIN}, an administrator",
    )


def test_a_label_from_anyone_else_is_no_approval() -> None:
    approved, why = approval([labelled("writer")], {ADMIN})

    assert not approved
    assert "set by writer, who is not an administrator" in why


def test_the_latest_label_event_counts() -> None:
    assert not approval([labelled(ADMIN), labelled("bot", event="unlabeled")], {ADMIN})[0]
    assert not approval([labelled(ADMIN), labelled("writer")], {ADMIN})[0]
    assert approval([labelled("writer"), labelled(ADMIN)], {ADMIN})[0]


def test_other_labels_and_no_events_are_no_approval() -> None:
    assert approval([labelled(ADMIN, label="ci:full")], {ADMIN}) == (
        False,
        f"the label {LABEL} is missing",
    )
    assert not approval([], {ADMIN})[0]
    assert not approval([labelled(ADMIN)], set())[0]


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


def test_the_gate_stops_a_decision_until_an_administrator_sets_the_label(
    git: Git, adr: Path, admins: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    adr.write_text(ADR.replace("1. The rule.", "1. A new rule."))
    commit_all(git, "docs(adr): change")
    arguments = ["--base", "main", "--head", "work", "--title", "docs(adr): change", *admins]

    assert (
        main([*arguments, "--events", events_file(tmp_path / "e", labelled(ADMIN, "ci:full"))]) == 1
    )
    out = capsys.readouterr().out
    assert "docs/adr/0099-example.md changed outside" in out
    assert "::error::" in out

    assert main([*arguments, "--events", events_file(tmp_path / "e", labelled("writer"))]) == 1
    assert "set by writer, who is not an administrator" in capsys.readouterr().out

    assert main([*arguments, "--events", events_file(tmp_path / "e", labelled(ADMIN))]) == 0
    assert "approved" in capsys.readouterr().out


def test_a_push_by_someone_else_withdraws_the_approval(
    git: Git, adr: Path, admins: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The workflow takes the label off; this run must not count it, whatever the events say."""
    adr.write_text(ADR.replace("1. The rule.", "1. A new rule."))
    commit_all(git, "docs(adr): change")
    events = events_file(tmp_path / "e", labelled(ADMIN))

    assert (
        main(["--base", "main", "--head", "work", *admins, "--events", events, "--withdrawn"]) == 1
    )
    assert "withdrawn" in capsys.readouterr().out


def test_the_gate_fails_closed_when_events_or_administrators_are_missing(
    git: Git, adr: Path, admins: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    adr.write_text(ADR.replace("1. The rule.", "1. A new rule."))
    commit_all(git, "docs(adr): change")
    events = events_file(tmp_path / "e", labelled(ADMIN))
    arguments = ["--base", "main", "--head", "work"]

    assert main([*arguments, *admins, "--events", str(tmp_path / "absent")]) == 1
    assert "cannot be read" in capsys.readouterr().out
    assert main([*arguments, "--admins", str(tmp_path / "absent"), "--events", events]) == 1
    assert "cannot be read" in capsys.readouterr().out
    assert main([*arguments, "--events", events]) == 1


def test_build_work_needs_no_events(git: Git, adr: Path) -> None:
    (adr.parents[2] / "app.py").write_text("x = 2\n")
    commit_all(git, "feat: build")

    assert main(["--base", "main", "--head", "work"]) == 0


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


def test_a_pull_argument_carries_the_number_and_its_events_file() -> None:
    assert parse_pull("266=events-266") == (266, "events-266")
    assert parse_pull("267=") == (267, "")


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
    git: Git, queue: Path, admins: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    none = events_file(tmp_path / "none")
    arguments = [
        "--base",
        "main",
        "--head",
        "queue",
        "--merge-group",
        *admins,
        "--pull",
        f"5={none}",
    ]

    assert (
        main(
            [*arguments, "--pull", f"6={events_file(tmp_path / 'e6', labelled(ADMIN, 'ci:full'))}"]
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "build work" in out
    assert "docs/adr/0100-new.md changed outside" in out
    assert f"::error::a decision waits for the product owner: the label {LABEL} is missing" in out

    assert (
        main([*arguments, "--pull", f"6={events_file(tmp_path / 'e6', labelled('writer'))}"]) == 1
    )
    assert "not an administrator" in capsys.readouterr().out

    assert main([*arguments, "--pull", f"6={events_file(tmp_path / 'e6', labelled(ADMIN))}"]) == 0
    assert "approved" in capsys.readouterr().out


def test_the_label_on_one_pull_request_does_not_cover_another_in_the_group(
    git: Git, queue: Path, admins: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each pull request is judged by its own commit, so the group's labels never pool."""
    arguments = ["--base", "main", "--head", "queue", "--merge-group", *admins]
    five = events_file(tmp_path / "e5", labelled(ADMIN))

    assert (
        main([*arguments, "--pull", f"5={five}", "--pull", f"6={events_file(tmp_path / 'e6')}"])
        == 1
    )
    assert "::error::" in capsys.readouterr().out


def test_the_gate_fails_closed_when_a_pull_requests_events_are_missing(
    git: Git, queue: Path, admins: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = ["--base", "main", "--head", "queue", "--merge-group", *admins]

    assert main([*arguments, "--pull", f"5={events_file(tmp_path / 'e5')}"]) == 1
    assert "events of pull request #6 were not given" in capsys.readouterr().out
    assert main([*arguments, "--pull", "5=", "--pull", f"6={tmp_path / 'absent'}"]) == 1
    assert "cannot be read" in capsys.readouterr().out


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
    gate = text[text.index("  gate:\n") :]
    assert "\n    if: github.event_name == 'pull_request_target'" not in gate, "job-level skip"
    assert "--merge-group" in text
    assert "--list-pull-requests" in text
    assert "github.event.merge_group.base_sha" in text


def test_in_the_queue_the_script_comes_from_the_same_commit_as_the_workflow() -> None:
    """ADR 0104 point 7: the queue's workflow is the group head's, so its script must be too.

    On 2026-11-02 the queue ran the new workflow (``--admins``) against ``main``'s old script
    and refused #414 with "unrecognized arguments".
    """
    text = WORKFLOW.read_text()

    assert (
        "ref: ${{ github.event.pull_request.base.sha || github.event.merge_group.head_sha }}"
        in text
    )
    assert "github.event.merge_group.head_sha" in text
    assert "pull-requests: read" in text


def test_the_workflow_reads_who_set_the_label_and_withdraws_it_after_a_foreign_push() -> None:
    """ADR 0104: the gate gets the label events and the administrators, never the bare labels."""
    text = WORKFLOW.read_text()

    assert "--labels" not in text, "the bare label list is no approval"
    assert "/issues/$1/events" in text
    assert "/collaborators/${login}/permission" in text
    assert "/collaborators/${SENDER}/permission" in text
    assert ".permissions.admin" not in text, "GITHUB_TOKEN gets an empty collaborator list (#442)"
    assert "github.event.action == 'synchronize'" in text
    assert "github.event.sender.login" in text
    assert "--withdrawn" in text
    assert "-X DELETE" in text
    assert "po%3Aapproved" in text
    assert "pull-requests: write" in text
    assert "needs: withdraw" in text
    assert "if: ${{ !cancelled() }}" in text, "a skipped gate counts as a passed check"
    assert "needs.withdraw.result == 'failure' && '--withdrawn'" in text


def test_nothing_cancels_a_withdrawal() -> None:
    """A later event (an edited title) must not cancel the run that takes the label off."""
    text = WORKFLOW.read_text()
    withdraw = text[text.index("  withdraw:\n") : text.index("  gate:\n")]

    assert not re.search(r"^concurrency:", text, re.M), "a workflow-level group cancels it"
    assert "concurrency" not in withdraw
    assert "actions/checkout" not in withdraw, "the job with write access runs no code"
    assert "--events" in text
    assert "--admins" in text
    assert 'label-events.sh" "$NUMBER"' in text
    assert 'label-events.sh" "$number"' in text
