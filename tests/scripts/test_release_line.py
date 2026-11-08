"""Maintenance lines release/X.Y (ADR 0116 point 3): the target, cutting, candidates, tags."""

from pathlib import Path

import pytest

from scripts import release_line, release_plan
from scripts.release_line import backport_lines, may_cut, newest_release, tag_target
from scripts.release_plan import MAIN, LineError, Target
from tests.scripts.conftest import Git, commit_all, cut_line

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


def test_an_option_shaped_label_is_only_a_label(capsys: pytest.CaptureFixture[str]) -> None:
    assert release_line.main(["backport-lines", "--", "--help", "backport:0.5", "ci:full"]) == 0
    assert capsys.readouterr().out == "0.5\n"


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
