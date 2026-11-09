"""Maintenance lines release/X.Y (ADR 0116 point 3): the target, cutting, candidates, tags."""

import json
from pathlib import Path

import pytest
import yaml

from scripts import release_line, release_plan
from scripts.release_line import (
    Backport,
    backport_lines,
    backports,
    backports_of,
    may_cut,
    newest_release,
    refusal,
    tag_target,
)
from scripts.release_plan import MAIN, LineError, Target
from tests.fakes.gh import FakeGh
from tests.scripts.conftest import Git, commit_all, cut_line, workflow_events

BACKPORT = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "backport.yml"

# The target: main or a line.


def test_a_line_is_release_slash_minor_and_a_release_pr_branch_is_not() -> None:
    assert Target.from_ref("release/0.5") == Target("0.5")
    assert Target.from_ref("refs/heads/release/1.12").branch == "release/1.12"
    assert Target.from_ref("origin/release/0.5").line == "0.5"
    assert Target.from_ref("release/v0.5.2") == MAIN
    assert Target.from_ref("release/0.5.1") == MAIN
    assert Target.from_ref("feat/x").branch == "main"


def test_a_line_admits_patches_of_its_own_minor_only() -> None:
    assert Target("0.5").admits("0.5.2")
    assert not Target("0.5").admits("0.5.0")
    assert not Target("0.5").admits("0.6.1")
    assert MAIN.admits("0.6.0")


def test_a_lines_next_release_is_the_next_patch() -> None:
    line = Target("0.5")

    assert line.next_version("0.5.1", ["fix: a", "chore: b"]) == "0.5.2"
    assert line.next_version("0.5.0", ["fix: a"]) == "0.5.1"
    assert line.next_version("0.5.1", []) is None
    assert MAIN.next_version("0.5.1", ["feat: a"]) == "0.6.0"


@pytest.mark.parametrize(
    ("last", "messages", "migrations", "reason"),
    [
        ("0.5.1", ["feat: new"], False, "takes fixes only"),
        ("0.5.1", ["fix!: breaking"], False, "takes fixes only"),
        ("0.5.1", ["fix: a"], True, "a migration changed"),
        ("0.6.0", ["fix: a"], False, "not on line"),
    ],
)
def test_a_line_refuses_a_feature_a_migration_or_another_minor(
    last: str, messages: list[str], migrations: bool, reason: str
) -> None:
    with pytest.raises(LineError, match=reason):
        Target("0.5").next_version(last, messages, migrations)


# Cutting, tagging and backport labels.


def test_a_line_starts_at_the_newest_release_of_its_minor() -> None:
    tags = ["v0.5.0", "v0.5.1", "v0.5.2-rc.1", "v0.5.10", "v0.6.0", "v0.50.1"]

    assert newest_release("0.5", tags) == "v0.5.10"
    assert newest_release("0.4", tags) is None


def test_a_line_is_cut_only_when_main_holds_a_feature_since_the_tag() -> None:
    assert may_cut(["fix: a", "feat: b"], "v0.5.0")
    assert not may_cut(["fix: a", "docs: b"], "v0.5.0")
    assert not may_cut([], "v0.5.0")


def test_a_tag_sits_on_main_or_as_a_patch_on_its_line() -> None:
    assert tag_target("0.6.0", on_main=True, on_line=False) == MAIN
    assert tag_target("0.5.2", on_main=False, on_line=True) == Target("0.5")
    assert tag_target("0.5.0", on_main=False, on_line=True) is None
    assert tag_target("0.5.2", on_main=False, on_line=False) is None


def test_backport_labels_name_lines() -> None:
    labels = ["backport:0.5", "ci:full", "backport:0.10", "backport:0.5", "backport:x"]

    assert backport_lines(labels) == ["0.5", "0.10"]


# In a repository: v0.5.0, then a feature on main; line release/0.5 cut from it.


@pytest.fixture
def lined(git: Git, tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "openplanner"\nversion = "0.5.0"\n')
    commit_all(git, "chore(release): v0.5.0 (#10)")
    git("tag", "-a", "v0.5.0", "-m", "v0.5.0")
    (tmp_path / "feature.txt").write_text("f\n")
    commit_all(git, "feat: a feature on main")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def test_cut_is_allowed_once_and_only_after_a_feature(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert release_line.main(["cut", "--line", "0.5"]) == 0
    assert capsys.readouterr().out == "tag=v0.5.0\nbranch=release/0.5\nlabel=backport:0.5\n"
    assert release_line.main(["cut", "--line", "0.4"]) == 1
    assert "no release v0.4.0" in capsys.readouterr().err
    assert release_line.main(["cut", "--line", "five"]) == 1
    assert "not a line X.Y" in capsys.readouterr().err

    cut_line(git, lined, "0.5", "v0.5.0")

    assert release_line.main(["cut", "--line", "0.5"]) == 1
    assert "exists already" in capsys.readouterr().err


def test_no_cut_without_a_feature_on_main(
    git: Git, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.txt").write_text("0\n")
    commit_all(git, "chore(release): v0.5.0 (#10)")
    git("tag", "-a", "v0.5.0", "-m", "v0.5.0")
    (tmp_path / "a.txt").write_text("1\n")
    commit_all(git, "fix: only a fix")
    git("update-ref", "refs/remotes/origin/main", "HEAD")

    assert release_line.main(["cut", "--line", "0.5"]) == 1
    assert "no line is needed" in capsys.readouterr().err


def test_a_patch_released_from_main_starts_the_line(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git("tag", "-a", "v0.5.1", "-m", "v0.5.1", "v0.5.0")

    assert release_line.main(["cut", "--line", "0.5"]) == 0
    assert capsys.readouterr().out.startswith("tag=v0.5.1\n")


def test_a_push_to_the_line_becomes_a_patch_candidate(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git("switch", "-q", "-c", "release/0.5", "v0.5.0")

    assert release_plan.main(["--candidate", "--branch", "release/0.5"]) == 0
    assert "nothing merged on release/0.5" in capsys.readouterr().err

    (lined / "fix.txt").write_text("fix\n")
    commit_all(git, "fix: the security fix (#21)")

    assert release_plan.main(["--candidate", "--branch", "release/0.5"]) == 0
    assert capsys.readouterr().out == "0.5.1-rc.1\n"

    git("tag", "-a", "v0.5.1-rc.1", "-m", "candidate")
    (lined / "fix2.txt").write_text("fix\n")
    commit_all(git, "fix: another fix (#22)")

    assert release_plan.main(["--candidate", "--branch", "release/0.5"]) == 0
    assert capsys.readouterr().out == "0.5.1-rc.2\n"


def test_a_cherry_picked_feature_makes_no_candidate(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cut_line(git, lined, "0.5", "v0.5.0")
    (lined / "more.txt").write_text("m\n")
    commit_all(git, "feat: does not belong on a line")

    assert release_plan.main(["--candidate", "--branch", "release/0.5"]) == 1
    assert "takes fixes only" in capsys.readouterr().err


def test_a_tag_is_allowed_on_main_or_its_own_line(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cut_line(git, lined, "0.5", "v0.5.0")
    fix = git("rev-parse", "HEAD").strip()
    feature = git("rev-parse", "origin/main").strip()

    assert release_line.main(["tag-allowed", "--version", "0.5.1", "--commit", fix]) == 0
    assert capsys.readouterr().out == "where=release/0.5\nlatest=false\n"
    assert release_line.main(["tag-allowed", "--version", "0.6.0", "--commit", feature]) == 0
    assert capsys.readouterr().out == "where=main\nlatest=true\n"
    assert release_line.main(["tag-allowed", "--version", "0.6.1", "--commit", fix]) == 1
    assert release_line.main(["tag-allowed", "--version", "0.5.0", "--commit", fix]) == 1
    assert "not on main and not a patch" in capsys.readouterr().err


def test_every_step_that_pipes_into_tee_fails_with_its_command() -> None:
    """main.yml has no default shell: `bash -e` without pipefail lets a refused tag through."""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "main.yml"
    ).read_text()
    blocks = workflow.split("\n      - ")

    piped = [block for block in blocks if '| tee -a "$GITHUB_OUTPUT"' in block]

    assert piped
    assert all("pipefail" in block for block in piped)


# ADR 0120: the backport starts from the merge to main, never from an event of a pull request.


def pull(number: int, *labels: str, merged: bool = True, base: str = "main") -> dict[str, object]:
    """A pull request as ``GET /repos/{repo}/pulls/{number}`` returns it."""
    return {
        "number": number,
        "title": f"fix: number {number}",
        "merged": merged,
        "merge_commit_sha": f"sha{number}",
        "base": {"ref": base},
        "labels": [{"name": label} for label in labels],
    }


PULLS = {
    5: pull(5, "ci:full", "backport:0.6", "backport:0.5"),
    6: pull(6, "ci:full"),
    7: pull(7, "backport:0.5", merged=False),
    8: pull(8, "backport:x"),
    9: pull(9, "backport:0.5", base="feature-x"),
}
FIVE = [
    Backport(5, "sha5", "fix: number 5", "0.5"),
    Backport(5, "sha5", "fix: number 5", "0.6"),
]


def test_a_merged_pull_request_gives_one_cherry_pick_per_line_its_labels_name() -> None:
    assert backports_of(PULLS[5]) == FIVE
    assert backports_of(PULLS[6]) == [], "no backport label"
    assert backports_of(PULLS[8]) == [], "backport:x names no line"


def test_only_a_pull_request_this_push_merged_into_main_can_be_backported() -> None:
    assert refusal(PULLS[5], {"sha5", "sha6"}) is None
    assert refusal(PULLS[5], None) is None, "a dispatch names no push"
    assert refusal(PULLS[7], None) == "it is not merged into main (yet)"
    assert refusal(PULLS[9], None) == "it is not merged into main (yet)", "merged elsewhere"
    # `Merge pull request #5 ... (#6)` names #5, whose merge commit is not in this push.
    assert refusal(PULLS[5], {"sha6"}) == (
        "its merge commit is none of the commits this push brought to main"
    )


def test_a_refused_pull_request_stops_the_run_and_is_never_skipped_quietly() -> None:
    for numbers, pushed in (([5, 7], None), ([9], None), ([5, 6], {"sha6"})):
        with pytest.raises(LineError, match="cannot be backported"):
            backports(numbers, "o/r", FakeGh(pulls=PULLS), pushed)


def test_each_pull_request_is_read_once() -> None:
    fake = FakeGh(pulls=PULLS)

    assert backports([6, 5, 8, 5], "o/r", fake, {"sha5", "sha6", "sha8"}) == FIVE
    assert fake.calls == [["api", f"repos/o/r/pulls/{n}"] for n in (5, 6, 8)]


def test_the_command_prints_the_matrix_as_one_json_list(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGh(pulls=PULLS)
    pushed = ["--pushed", "sha5", "sha6"]

    assert (
        release_line.main(["backports", "--repository", "o/r", "--pull", "6", "5", *pushed], fake)
        == 0
    )
    assert json.loads(capsys.readouterr().out) == [
        {"number": 5, "commit": "sha5", "title": "fix: number 5", "line": "0.5"},
        {"number": 5, "commit": "sha5", "title": "fix: number 5", "line": "0.6"},
    ]
    assert release_line.main(["backports", "--repository", "o/r", "--pull", "6"], fake) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert release_line.main(["backports", "--repository", "o/r", "--pull", "7"], fake) == 1
    assert "#7 cannot be backported" in capsys.readouterr().err


def test_a_dispatch_with_anything_but_a_number_stops() -> None:
    for value in ("5;touch", "-5", "²", "*", ""):
        with pytest.raises(SystemExit):
            release_line.main(["backports", "--repository", "o/r", "--pull", value], FakeGh())


def test_the_backport_starts_from_a_merge_to_main_or_a_dispatch() -> None:
    events = workflow_events(BACKPORT)
    find = yaml.safe_load(BACKPORT.read_text())["jobs"]["find"]
    script = find["steps"][-1]["run"]

    assert set(events) == {"push", "workflow_dispatch"}, "never pull_request_target"
    assert events["push"] == {"branches": ["main"]}
    assert events["workflow_dispatch"]["inputs"]["pull_request"]["required"] is True
    assert "environment" not in find
    assert "secrets." not in json.dumps(find), "the job that reads pull requests holds no secret"
    assert find["steps"][0]["with"]["fetch-depth"] == 0, "the commits of the push"
    # The pull requests of a push come from the decision gate's own walk; a commit that names
    # none fails there (tests/scripts/test_decision_gate.py).
    assert "python3 -m scripts.decision_gate" in script
    assert "--list-pull-requests" in script
    assert "python3 -m scripts.release_line backports" in script
    assert "--pushed" in script, "each listed pull request must be one this push merged"


def test_the_job_with_the_key_runs_once_per_entry_and_reads_nothing_again() -> None:
    """One matrix entry per pull request and line: a failing backport stops no other."""
    job = yaml.safe_load(BACKPORT.read_text())["jobs"]["backport"]
    step = job["steps"][-1]
    from_matrix = {name.lower() for name, value in step["env"].items() if "matrix." in str(value)}

    assert job["needs"] == "find"
    assert job["if"] == "needs.find.outputs.backports != '[]'"
    assert job["environment"] == "release"
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"]["include"] == "${{ fromJSON(needs.find.outputs.backports) }}"
    assert from_matrix == set(Backport.__dataclass_fields__)
    assert "gh api" not in step["run"]
    assert "gh pr view" not in step["run"]
    assert "github.event.pull_request" not in json.dumps(job)
