"""Open build steps become issues, finished ones close them, and drift is reported (ADR 0078)."""

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from scripts.adr_issues import (
    REPORT_TITLE,
    Action,
    GitHub,
    Issue,
    ParseError,
    Step,
    drift,
    main,
    parse_adr,
    parse_overview,
    plan,
    render,
)
from tests.scripts.conftest import fake_program, script_path

ADR = """# 0099. An example

Status: accepted (2026-10-31). Partly built; see *Build status*.

## Decision

1. The rule.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #1 | merged |
| 1 | The first thing, with `code` | #2 | in PR |
| 2 | The second thing | | planned |
| 3–4 | Later things | – | follow-up; decided after step 2 is in use |

## Consequences

What follows.
"""

OVERVIEW = """| Built? | ADRs |
|---|---|
| PARTIAL | 1 |

| ADR | Title | Built? | Evidence | What is missing |
|---|---|---|---|---|
| 0099 | An example | PARTIAL | #1 | steps 2 to 4 |
| 0100 | Another | BUILT | #3 | – |
| 0101 | A third | NOT BUILT (policy part) | none | everything |
"""


def step(number: str, state: str, pr: str = "", adr: str = "0099", what: str = "x") -> Step:
    return Step(
        adr=adr,
        title="An example",
        file=f"{adr}-example.md",
        step=number,
        what=what,
        pr=pr,
        state=state,
    )


def issue(
    number: int, title: str, state: str = "OPEN", labels: Sequence[str] = ("adr:0099",)
) -> Issue:
    return Issue(number=number, title=title, state=state, labels=frozenset(labels))


# --- parsing -----------------------------------------------------------------


def test_parse_adr_reads_every_row_with_its_state_word() -> None:
    steps = parse_adr(ADR, "0099-example.md")

    assert [(s.step, s.status, s.pr_number) for s in steps] == [
        ("0", "merged", 1),
        ("1", "in PR", 2),
        ("2", "planned", None),
        ("3–4", "follow-up", None),
    ]
    assert steps[1].title == "An example"
    assert steps[1].issue_title == "ADR 0099 step 1: The first thing, with `code`"
    assert steps[1].key == ("0099", "1")


def test_an_adr_without_a_table_has_no_steps() -> None:
    assert parse_adr("# 0001. Old\n\nStatus: accepted.\n", "0001-old.md") == []


def test_an_unknown_state_is_a_parse_error() -> None:
    with pytest.raises(ParseError, match="0099-example.md step 2"):
        parse_adr(ADR.replace("| planned |", "| done soon |"), "0099-example.md")


def test_parse_overview_reads_the_category_per_adr() -> None:
    assert parse_overview(OVERVIEW) == {"0099": "PARTIAL", "0100": "BUILT", "0101": "NOT BUILT"}


def test_an_unknown_category_is_a_parse_error() -> None:
    with pytest.raises(ParseError, match="0100"):
        parse_overview(OVERVIEW.replace("| BUILT |", "| DONE |"))


def test_an_issue_knows_its_adr_and_step_from_the_title() -> None:
    assert issue(7, "ADR 0099 step 3–4: Later things").key == ("0099", "3–4")
    assert issue(7, "Something else").key is None
    assert issue(7, "ADR 0099 step 1: x", labels=["bug"]).adr_label is None
    assert issue(7, "ADR 0099 step 1: x").adr_label == "adr:0099"


# --- the plan ----------------------------------------------------------------


def test_open_rows_without_an_issue_are_created_and_nothing_else_happens() -> None:
    steps = [step("0", "merged", "#1"), step("1", "in PR", "#2"), step("2", "planned")]

    actions = plan(steps, [])

    assert [(a.kind, a.step.step if a.step else None) for a in actions] == [("create", "2")]


def test_a_finished_row_closes_its_open_issue() -> None:
    open_issue = issue(5, "ADR 0099 step 2: x")

    actions = plan([step("2", "built")], [open_issue])

    assert [(a.kind, a.issue) for a in actions] == [("close", open_issue)]


def test_running_twice_changes_nothing() -> None:
    steps = [step("2", "planned"), step("3", "merged")]
    issues = [issue(5, "ADR 0099 step 2: x"), issue(6, "ADR 0099 step 3: x", state="CLOSED")]

    assert plan(steps, issues) == []


def test_an_issue_without_the_adr_label_is_never_touched() -> None:
    foreign = issue(5, "ADR 0099 step 2: x", labels=["enhancement"])

    actions = plan([step("2", "merged")], [foreign])

    assert actions == []


def test_a_closed_issue_for_an_open_row_is_reopened_not_duplicated() -> None:
    closed = issue(5, "ADR 0099 step 2: x", state="CLOSED")

    actions = plan([step("2", "planned")], [closed])

    assert [(a.kind, a.issue) for a in actions] == [("reopen", closed)]


def test_a_changed_what_retitles_the_issue() -> None:
    stale = issue(5, "ADR 0099 step 2: old words")

    actions = plan([step("2", "planned", what="new words")], [stale])

    assert [(a.kind, a.issue) for a in actions] == [("retitle", stale)]
    assert actions[0].step is not None
    assert actions[0].step.issue_title == "ADR 0099 step 2: new words"


def test_step_0_is_the_adr_itself_and_gets_no_issue() -> None:
    assert plan([step("0", "planned", what="ADR proposed")], []) == []


def test_a_row_in_a_pr_keeps_its_issue_open_for_the_pr_to_close() -> None:
    assert plan([step("1", "in PR", "#2")], [issue(5, "ADR 0099 step 1: x")]) == []


def test_an_action_says_what_it_does() -> None:
    assert str(Action("create", step("2", "planned"), None)) == (
        "create: ADR 0099 step 2: x (label adr:0099)"
    )
    assert str(Action("close", step("2", "built"), issue(5, "ADR 0099 step 2: x"))) == (
        "close #5: ADR 0099 step 2: x (row says built)"
    )


# --- drift -------------------------------------------------------------------


def pr_state(number: int) -> str:
    return {1: "MERGED", 2: "OPEN", 3: "CLOSED"}.get(number, "UNKNOWN")


def test_no_drift_renders_an_empty_report() -> None:
    found = drift([step("2", "planned")], {"0099": "PARTIAL"}, [], pr_state)

    assert found.empty
    assert "nothing" in render(found, date(2026, 10, 31)).lower()


def test_a_built_adr_with_open_rows_and_a_not_built_adr_with_merged_rows_are_drift() -> None:
    steps = [
        step("1", "merged", "#1", adr="0100"),
        step("2", "planned", adr="0100"),
        step("0", "merged", "#1", adr="0101", what="ADR proposed"),
        step("1", "merged", "#1", adr="0101"),
    ]

    found = drift(steps, {"0100": "BUILT", "0101": "NOT BUILT"}, [], pr_state)

    assert found.overview == [
        "ADR 0100 is BUILT in `docs/adr-status.md`, but step 2 is `planned`",
        "ADR 0101 is NOT BUILT in `docs/adr-status.md`, but step 1 is `merged`",
    ]


def test_a_row_in_a_merged_or_closed_pr_is_drift_and_an_open_pr_is_not() -> None:
    steps = [step("1", "in PR", "#1"), step("2", "in PR", "#2"), step("3", "in PR", "#3")]

    found = drift(steps, {"0099": "PARTIAL"}, [], pr_state)

    assert found.stale_prs == [
        "ADR 0099 step 1 says `in PR` #1, but that pull request is merged",
        "ADR 0099 step 3 says `in PR` #3, but that pull request is closed",
    ]


def test_an_issue_whose_row_is_gone_or_unreadable_is_drift() -> None:
    issues = [
        issue(5, "ADR 0099 step 9: gone"),
        issue(6, "No step in this title"),
        issue(7, "ADR 0099 step 2: fine"),
        issue(8, "ADR 0099 step 2: a second one"),
        issue(9, "ADR 0099 step 9: closed and gone", state="CLOSED"),
        issue(10, "ADR 0099 step 9: not ours", labels=["bug"]),
    ]

    found = drift([step("2", "planned")], {"0099": "PARTIAL"}, issues, pr_state)

    assert found.orphans == [
        "#5 `ADR 0099 step 9: gone`: no such row in `docs/adr/0099-example.md`",
        "#6 `No step in this title`: labelled `adr:0099` but not titled `ADR NNNN step S: …`",
        "#8 `ADR 0099 step 2: a second one`: a second issue for the row of #7",
    ]


def test_the_report_is_markdown_with_one_section_per_check() -> None:
    steps = [step("1", "in PR", "#1"), step("2", "planned", adr="0100")]

    text = render(
        drift(steps, {"0099": "PARTIAL", "0100": "BUILT"}, [], pr_state), date(2026, 10, 31)
    )

    assert text.startswith("# ADR drift 2026-10-31")
    assert "## Overview disagrees with a Build status table" in text
    assert "- ADR 0100 is BUILT" in text
    assert "## Rows that say in PR for a pull request that is gone" in text
    assert "- ADR 0099 step 1" in text
    assert "## Issues without a row" not in text


# --- the gh layer ------------------------------------------------------------


class FakeGh:
    """Answers `gh` reads from canned data and records every call."""

    def __init__(self, labels: Sequence[str] = (), issues: Sequence[Issue] = ()) -> None:
        self.labels = list(labels)
        self.issues = list(issues)
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        self.calls.append(list(args))
        match list(args[:2]):
            case ["label", "list"]:
                return json.dumps([{"name": n} for n in self.labels])
            case ["issue", "list"]:
                return json.dumps(
                    [
                        {
                            "number": i.number,
                            "title": i.title,
                            "state": i.state,
                            "labels": [{"name": n} for n in i.labels],
                        }
                        for i in self.issues
                    ]
                )
            case ["issue", "create"]:
                return "https://github.com/o/r/issues/42\n"
            case ["pr", "view"]:
                return json.dumps({"state": pr_state(int(args[2]))})
        return ""


def test_the_github_layer_reads_labels_issues_and_pr_states() -> None:
    fake = FakeGh(["bug", "adr:0099"], [issue(5, "ADR 0099 step 2: x")])
    gh = GitHub(fake)

    assert gh.labels() == {"bug", "adr:0099"}
    assert gh.issues() == [issue(5, "ADR 0099 step 2: x")]
    assert gh.pr_state(1) == "MERGED"


def test_the_github_layer_writes_through_gh_with_fixed_arguments() -> None:
    fake = FakeGh()
    gh = GitHub(fake)

    gh.create_label("adr:0099", "Build steps of ADR 0099")
    assert gh.create_issue("ADR 0099 step 2: x", "body", "adr:0099") == 42
    gh.close_issue(5, "done")
    gh.reopen_issue(5, "again")
    gh.retitle(5, "new")
    gh.edit_issue(7, "ADR drift 2026-10-31", "report")

    assert fake.calls == [
        [
            "label",
            "create",
            "adr:0099",
            "--color",
            "5319e7",
            "--description",
            "Build steps of ADR 0099",
        ],
        [
            "issue",
            "create",
            "--title",
            "ADR 0099 step 2: x",
            "--body",
            "body",
            "--label",
            "adr:0099",
        ],
        ["issue", "close", "5", "--comment", "done"],
        ["issue", "reopen", "5", "--comment", "again"],
        ["issue", "edit", "5", "--title", "new"],
        ["issue", "edit", "7", "--title", "ADR drift 2026-10-31", "--body", "report"],
    ]


# --- main --------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A docs tree with one ADR whose steps 2 and 3–4 are open and an overview."""
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0099-example.md").write_text(ADR)
    (tmp_path / "docs" / "adr-status.md").write_text(OVERVIEW)
    return tmp_path


def test_sync_creates_the_label_once_and_an_issue_per_open_row(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGh(labels=["bug"])

    assert main([], root=repo, run=fake) == 0

    writes = [c for c in fake.calls if c[1] in ("create", "close", "reopen", "edit")]
    assert writes[0][:3] == ["label", "create", "adr:0099"]
    assert [c[3] for c in writes[1:]] == [
        "ADR 0099 step 2: The second thing",
        "ADR 0099 step 3–4: Later things",
    ]
    body = writes[1][5]
    assert "docs/adr/0099-example.md" in body
    assert "Closes #N" in body
    assert "ADR 0099 step 2: The second thing" in capsys.readouterr().out


def test_dry_run_writes_nothing_and_says_what_it_would_do(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "docs" / "adr" / "0099-example.md").write_text(ADR.replace("| in PR |", "| merged |"))
    fake = FakeGh(labels=["adr:0099"], issues=[issue(5, "ADR 0099 step 1: The first thing")])

    assert main(["--dry-run"], root=repo, run=fake) == 0

    assert [c[:2] for c in fake.calls] == [["label", "list"], ["issue", "list"]]
    out = capsys.readouterr().out
    assert "would close #5" in out
    assert "would create: ADR 0099 step 2" in out


def test_sync_with_nothing_to_do_says_so(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeGh(
        labels=["adr:0099"],
        issues=[
            issue(5, "ADR 0099 step 2: The second thing"),
            issue(6, "ADR 0099 step 3–4: Later things"),
        ],
    )

    assert main([], root=repo, run=fake) == 0
    assert [c[:2] for c in fake.calls] == [["label", "list"], ["issue", "list"]]
    assert "nothing to do" in capsys.readouterr().out


def test_report_prints_the_drift_and_posts_it_as_one_issue(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "docs" / "adr-status.md").write_text(
        OVERVIEW.replace("| 0099 | An example | PARTIAL", "| 0099 | An example | BUILT")
    )
    fake = FakeGh(issues=[issue(5, "ADR 0099 step 7: gone")])

    assert main(["--report", "--post", "--today", "2026-10-31"], root=repo, run=fake) == 0

    out = capsys.readouterr().out
    assert "# ADR drift 2026-10-31" in out
    assert "ADR 0099 is BUILT" in out
    assert "#5 `ADR 0099 step 7: gone`" in out
    created = [c for c in fake.calls if c[:2] == ["issue", "create"]]
    assert created[0][3] == "ADR drift 2026-10-31"
    assert "ADR 0099 is BUILT" in created[0][5]
    assert created[0][7] == REPORT_TITLE.lower().replace(" ", ":")


def test_report_updates_the_open_report_issue_instead_of_opening_a_second(repo: Path) -> None:
    (repo / "docs" / "adr-status.md").write_text(
        OVERVIEW.replace("| 0099 | An example | PARTIAL", "| 0099 | An example | BUILT")
    )
    fake = FakeGh(issues=[issue(9, "ADR drift 2026-10-24", labels=["adr:drift"])])

    assert main(["--report", "--post", "--today", "2026-10-31"], root=repo, run=fake) == 0

    writes = [c for c in fake.calls if c[1] in ("create", "close", "edit")]
    assert len(writes) == 1
    assert writes[0][:5] == ["issue", "edit", "9", "--title", "ADR drift 2026-10-31"]


def test_an_empty_report_closes_the_open_report_issue_and_creates_none(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGh(issues=[issue(9, "ADR drift 2026-10-24", labels=["adr:drift"])])

    assert main(["--report", "--post", "--today", "2026-10-31"], root=repo, run=fake) == 0

    writes = [c for c in fake.calls if c[1] in ("create", "close", "edit")]
    assert [c[:3] for c in writes] == [["issue", "close", "9"]]
    assert "nothing" in capsys.readouterr().out.lower()


def test_report_without_post_only_prints(repo: Path) -> None:
    fake = FakeGh()

    assert main(["--report"], root=repo, run=fake) == 0
    assert [c[:2] for c in fake.calls] == [["issue", "list"], ["pr", "view"]]


def test_a_parse_error_exits_non_zero(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "docs" / "adr" / "0099-example.md").write_text(ADR.replace("| planned |", "| soon |"))

    assert main(["--report"], root=repo, run=FakeGh()) == 2
    assert "::error::" in capsys.readouterr().out


def test_the_script_runs_gh_from_the_path(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "gh.log"
    (tmp_path / "bin").mkdir()
    reads = '"label list") echo "[]";; "issue list") echo "[]";;'
    fakes = fake_program(
        tmp_path / "bin", "gh", f'echo "$@" >> "{log}"\ncase "$1 $2" in {reads} esac'
    )
    monkeypatch.setenv("PATH", script_path(fakes))

    assert main(["--dry-run"], root=repo) == 0

    assert log.read_text().splitlines()[0].startswith("label list --json name")
    assert "would create: ADR 0099 step 2" in capsys.readouterr().out


def test_counts_prints_the_adrs_per_category_and_calls_no_one(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGh()

    assert main(["--counts"], root=repo, run=fake) == 0

    assert capsys.readouterr().out.splitlines() == [
        "BUILT: 1",
        "PARTIAL: 1",
        "NOT BUILT: 1",
        "POLICY: 0",
    ]
    assert fake.calls == []
