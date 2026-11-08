"""Maintenance lines release/X.Y (ADR 0116 point 3): cut, candidates, release, tag, changelog."""

from pathlib import Path

import pytest

from scripts import release_line, release_pr
from scripts.release_line import (
    LineError,
    backport_lines,
    belongs_to,
    candidate_base,
    line_of,
    may_cut,
    merge_changelogs,
    newest_release,
    tag_allowed,
)
from scripts.release_pr import plan
from tests.scripts.conftest import Git, commit_all

PYPROJECT = '[project]\nname = "openplanner"\nversion = "0.5.0"\n'
LOCK = '[[package]]\nname = "openplanner"\nversion = "0.5.0"\nsource = { virtual = "." }\n'
PUBLICCODE = 'softwareVersion: "0.5.0"\nreleaseDate: "2026-11-06"\n'
CHANGELOG = "# Changelog\n\n## Unreleased\n\n## 0.5.0 (2026-11-06)\n\n- Base.\n"


# The rules.


def test_a_line_is_release_slash_minor_and_a_release_pr_branch_is_not() -> None:
    assert line_of("release/0.5") == "0.5"
    assert line_of("refs/heads/release/1.12") == "1.12"
    assert line_of("release/v0.5.2") is None
    assert line_of("main") is None
    assert line_of("release/0.5.1") is None


def test_a_line_takes_patches_of_its_own_minor_only() -> None:
    assert belongs_to("0.5.2", "0.5")
    assert not belongs_to("0.5.0", "0.5")
    assert not belongs_to("0.6.1", "0.5")


def test_a_lines_candidate_is_the_next_patch() -> None:
    assert candidate_base("0.5", "0.5.1", ["fix: a", "chore: b"], False) == "0.5.2"
    assert candidate_base("0.5", "0.5.0", ["fix: a"], False) == "0.5.1"
    assert candidate_base("0.5", "0.5.1", [], False) is None


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
        candidate_base("0.5", last, messages, migrations)


def test_a_line_is_cut_only_when_main_holds_a_feature_since_the_tag() -> None:
    assert may_cut(["fix: a", "feat: b"], "v0.5.0")
    assert not may_cut(["fix: a", "docs: b"], "v0.5.0")
    assert not may_cut([], "v0.5.0")


def test_a_line_starts_at_the_newest_release_of_its_minor() -> None:
    tags = ["v0.5.0", "v0.5.1", "v0.5.2-rc.1", "v0.5.10", "v0.6.0", "v0.50.1"]

    assert newest_release("0.5", tags) == "v0.5.10"
    assert newest_release("0.4", tags) is None


def test_a_tag_sits_on_main_or_as_a_patch_on_its_line() -> None:
    assert tag_allowed("0.6.0", on_main=True, on_line=False)
    assert tag_allowed("0.5.2", on_main=False, on_line=True)
    assert not tag_allowed("0.5.0", on_main=False, on_line=True)
    assert not tag_allowed("0.5.2", on_main=False, on_line=False)


def test_backport_labels_name_lines() -> None:
    labels = ["backport:0.5", "ci:full", "backport:0.10", "backport:0.5", "backport:x"]

    assert backport_lines(labels) == ["0.5", "0.10"]


def test_the_lines_sections_join_main_in_version_order() -> None:
    main = (
        "# Changelog\n\n## Unreleased\n\n## 0.6.0 (2026-11-20)\n\n- New.\n\n"
        "## 0.5.0 (x)\n\n- Base.\n"
    )
    line = (
        "# Changelog\n\n## Unreleased\n\n## 0.5.2 (y)\n\n- Fix 2.\n\n"
        "## 0.5.1 (z)\n\n- Fix 1.\n\n## 0.5.0 (x)\n\n- Other.\n"
    )

    merged = merge_changelogs(main, [line])

    assert merged == (
        "# Changelog\n\n## Unreleased\n\n## 0.6.0 (2026-11-20)\n\n- New.\n\n"
        "## 0.5.2 (y)\n\n- Fix 2.\n\n## 0.5.1 (z)\n\n- Fix 1.\n\n## 0.5.0 (x)\n\n- Base.\n"
    )
    assert merge_changelogs(merged, [line]) == merged


def test_the_release_plan_on_a_line_is_a_patch_of_it() -> None:
    assert plan("v0.5.1", ["fix: a"], None, line="0.5") == "0.5.2"
    assert plan("v0.5.1", ["fix: a"], "0.5.3", line="0.5") == "0.5.3"
    with pytest.raises(LineError, match="not a patch of line 0.5"):
        plan("v0.5.1", ["fix: a"], "0.6.0", line="0.5")
    with pytest.raises(LineError, match="takes fixes only"):
        plan("v0.5.1", ["feat: a"], None, line="0.5")


# In a repository: main moved on with a feature, line release/0.5 cut from v0.5.0.


@pytest.fixture
def lined(git: Git, tmp_path: Path) -> Path:
    for name, text in (
        ("pyproject.toml", PYPROJECT),
        ("uv.lock", LOCK),
        ("publiccode.yml", PUBLICCODE),
        ("CHANGELOG.md", CHANGELOG),
    ):
        (tmp_path / name).write_text(text)
    commit_all(git, "chore(release): v0.5.0 (#10)")
    git("tag", "-a", "v0.5.0", "-m", "v0.5.0")
    (tmp_path / "feature.txt").write_text("f\n")
    commit_all(git, "feat: a feature on main")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def _cut_line(git: Git, root: Path) -> None:
    git("switch", "-q", "-c", "release/0.5", "v0.5.0")
    (root / "fix.txt").write_text("fix\n")
    commit_all(git, "fix: the security fix (#21)")
    git("update-ref", "refs/remotes/origin/release/0.5", "HEAD")


def test_cut_is_allowed_once_and_only_after_a_feature(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert release_line.main(["cut", "--line", "0.5"]) == 0
    assert capsys.readouterr().out == "tag=v0.5.0\nbranch=release/0.5\nlabel=backport:0.5\n"
    assert release_line.main(["cut", "--line", "0.4"]) == 1
    assert "no release v0.4.0" in capsys.readouterr().err

    _cut_line(git, lined)

    assert release_line.main(["cut", "--line", "0.5"]) == 1
    assert "exists already" in capsys.readouterr().err


def test_no_cut_without_a_feature_on_main(
    git: Git, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    commit_all(git, "chore(release): v0.5.0 (#10)")
    git("tag", "-a", "v0.5.0", "-m", "v0.5.0")
    (tmp_path / "a.txt").write_text("a\n")
    commit_all(git, "fix: only a fix")
    git("update-ref", "refs/remotes/origin/main", "HEAD")

    assert release_line.main(["cut", "--line", "0.5"]) == 1
    assert "no line is needed" in capsys.readouterr().err


def test_a_push_to_the_line_becomes_a_patch_candidate(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _cut_line(git, lined)

    assert release_line.main(["candidate", "--branch", "release/0.5"]) == 0
    assert capsys.readouterr().out == "0.5.1-rc.1\n"

    git("tag", "-a", "v0.5.1-rc.1", "-m", "candidate")
    (lined / "fix2.txt").write_text("fix\n")
    commit_all(git, "fix: another fix (#22)")

    assert release_line.main(["candidate", "--branch", "release/0.5"]) == 0
    assert capsys.readouterr().out == "0.5.1-rc.2\n"
    assert release_line.main(["candidate", "--branch", "main"]) == 1


def test_a_cherry_picked_feature_makes_no_candidate(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _cut_line(git, lined)
    (lined / "more.txt").write_text("m\n")
    commit_all(git, "feat: does not belong on a line")

    assert release_line.main(["candidate", "--branch", "release/0.5"]) == 1
    assert "takes fixes only" in capsys.readouterr().err


def test_a_tag_is_allowed_on_main_or_its_own_line(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _cut_line(git, lined)
    fix = git("rev-parse", "HEAD").strip()
    feature = git("rev-parse", "origin/main").strip()

    assert release_line.main(["tag-allowed", "--version", "0.5.1", "--commit", fix]) == 0
    assert release_line.main(["tag-allowed", "--version", "0.6.0", "--commit", feature]) == 0
    assert release_line.main(["tag-allowed", "--version", "0.6.1", "--commit", fix]) == 1
    assert release_line.main(["tag-allowed", "--version", "0.5.0", "--commit", fix]) == 1
    assert "not on main and not a patch" in capsys.readouterr().err


def test_the_release_pr_on_a_line_and_its_tag(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _cut_line(git, lined)

    assert release_pr.main(["plan", "--base", "release/0.5"]) == 0
    out = capsys.readouterr().out
    assert "version=0.5.1" in out
    assert "base=release/0.5" in out
    assert "audit=false" in out

    (lined / "pyproject.toml").write_text(PYPROJECT.replace("0.5.0", "0.5.1"))
    commit_all(git, "chore(release): v0.5.1 (#23)")
    git("update-ref", "refs/remotes/origin/release/0.5", "HEAD")
    args = ["--branch", "origin/release/0.5", "--actor", "po", "--date", "2026-11-09"]

    assert release_pr.main(["tag-check", "--version", "0.5.1", *args]) == 0
    assert "number=23" in capsys.readouterr().out
    assert release_pr.main(["tag-check", "--version", "0.6.1", *args]) == 1
    assert "not a patch of line 0.5" in capsys.readouterr().err


def test_the_next_release_on_main_takes_over_the_lines_changelog(
    git: Git, lined: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _cut_line(git, lined)
    (lined / "CHANGELOG.md").write_text(
        CHANGELOG.replace("## 0.5.0", "## 0.5.1 (2026-11-09)\n\n- The security fix.\n\n## 0.5.0")
    )
    commit_all(git, "chore(release): v0.5.1 (#23)")
    git("update-ref", "refs/remotes/origin/release/0.5", "HEAD")
    git("switch", "-q", "main")
    body = lined / "body.md"

    code = release_pr.main(
        ["prepare", "--version", "0.6.0", "--body", str(body), "--date", "2026-11-20"]
    )

    assert code == 0, capsys.readouterr().err
    assert (lined / "CHANGELOG.md").read_text() == (
        "# Changelog\n\n## Unreleased\n\n## 0.6.0 (2026-11-20)\n\n"
        "## 0.5.1 (2026-11-09)\n\n- The security fix.\n\n## 0.5.0 (2026-11-06)\n\n- Base.\n"
    )


def test_backport_lines_prints_one_line_each(capsys: pytest.CaptureFixture[str]) -> None:
    assert release_line.main(["backport-lines", "backport:0.5", "ci:full"]) == 0
    assert capsys.readouterr().out == "0.5\n"


def test_a_patch_released_from_main_starts_the_line(
    git: Git, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    commit_all(git, "chore(release): v0.5.0 (#10)")
    git("tag", "-a", "v0.5.0", "-m", "v0.5.0")
    (tmp_path / "fix.txt").write_text("1\n")
    commit_all(git, "chore(release): v0.5.1 (#11)")
    git("tag", "-a", "v0.5.1", "-m", "v0.5.1")
    (tmp_path / "feature.txt").write_text("f\n")
    commit_all(git, "feat: later")
    git("update-ref", "refs/remotes/origin/main", "HEAD")

    assert release_line.main(["cut", "--line", "0.5"]) == 0
    assert capsys.readouterr().out.startswith("tag=v0.5.1\n")
