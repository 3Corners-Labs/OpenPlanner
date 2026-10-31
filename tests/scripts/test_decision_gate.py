"""Build work passes the decision gate, a decision waits for the label (ADR 0078)."""

from pathlib import Path

import pytest

from scripts.decision_gate import LABEL, adr_changed_decision, decision_part, main, reasons
from tests.scripts.conftest import Git, commit_all

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
