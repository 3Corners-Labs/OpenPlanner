"""The release workflow's file edits, PR body and tag checks (ADR 0079, ADR 0128)."""

import re
from pathlib import Path

import pytest

from scripts.release_plan import LineError, Target
from scripts.release_pr import (
    adr_progress,
    bump_lock,
    bump_publiccode,
    bump_pyproject,
    main,
    merge_changelogs,
    parse_version,
    plan,
    pr_body,
    release_changelog,
    release_commit,
    tag_message,
)
from tests.scripts.conftest import Git, commit_all, cut_line

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / ".github" / "pull_request_template.md"

PYPROJECT = '[project]\nname = "openplanner"\nversion = "0.3.0"\nrequires-python = ">=3.12"\n'
LOCK = (
    'version = 1\n\n[[package]]\nname = "httpx"\nversion = "0.28.1"\n\n'
    '[[package]]\nname = "openplanner"\nversion = "0.3.0"\nsource = { virtual = "." }\n'
)
CHANGELOG = (
    "# Changelog\n\n## Unreleased\n\n### Features\n\n- A thing.\n\n"
    "## 0.3.0 (2026-10-29)\n\n- Old.\n"
)


# The number.


def test_a_version_is_three_numbers() -> None:
    assert parse_version("1.2.3") == (1, 2, 3)
    for wrong in ("v1.2.3", "1.2", "1.2.3-rc.1", "", "latest"):
        with pytest.raises(ValueError, match="not a version"):
            parse_version(wrong)


def test_the_plan_follows_the_commits_unless_a_higher_number_is_given() -> None:
    assert plan("v0.3.0", ["feat: a"], None) == "0.4.0"
    assert plan("v0.3.0", ["fix: a"], None) == "0.3.1"
    # The product owner may choose a higher number than the rules give, never a lower one.
    assert plan("v0.3.0", ["fix: a"], "0.4.0") == "0.4.0"
    assert plan("v0.3.0", ["fix: a"], "1.0.0") == "1.0.0"
    with pytest.raises(ValueError, match="not above the last release v0.3.0"):
        plan("v0.3.0", ["feat: a"], "0.3.0")
    with pytest.raises(ValueError, match="not above"):
        plan("v0.3.0", ["feat: a"], "0.2.9")
    with pytest.raises(ValueError, match="not a version"):
        plan("v0.3.0", ["feat: a"], "v0.4.0")
    with pytest.raises(ValueError, match="nothing to release"):
        plan("v0.3.0", [], None)


# The three files.


def test_pyproject_gets_the_version_and_nothing_else_changes() -> None:
    bumped = bump_pyproject(PYPROJECT, "0.4.0")

    assert bumped == PYPROJECT.replace('version = "0.3.0"', 'version = "0.4.0"')
    with pytest.raises(ValueError, match="no `version"):
        bump_pyproject('[project]\nname = "x"\n', "0.4.0")


def test_the_lock_changes_only_the_projects_own_line() -> None:
    bumped = bump_lock(LOCK, "0.4.0")

    assert 'name = "openplanner"\nversion = "0.4.0"' in bumped
    assert 'name = "httpx"\nversion = "0.28.1"' in bumped
    assert bumped.count("0.4.0") == 1
    with pytest.raises(ValueError, match="no package entry"):
        bump_lock('[[package]]\nname = "httpx"\nversion = "0.28.1"\n', "0.4.0")


def test_unreleased_becomes_the_release_with_a_fresh_unreleased_above_it() -> None:
    released = release_changelog(CHANGELOG, "0.4.0", "2026-11-02")

    assert released.startswith(
        "# Changelog\n\n## Unreleased\n\n## 0.4.0 (2026-11-02)\n\n### Features"
    )
    assert "- A thing." in released
    assert released.count("## Unreleased") == 1
    with pytest.raises(ValueError, match="no `## Unreleased`"):
        release_changelog("# Changelog\n\n## 0.3.0\n", "0.4.0", "2026-11-02")


# The pull request body.


def _template_headings() -> list[str]:
    return re.findall(r"^## .+$", TEMPLATE.read_text(), re.M)


def test_the_body_answers_every_section_of_the_pr_template() -> None:
    body = pr_body("0.4.0", "v0.3.0", ["feat: a", "fix: b"], "cyberbeuk", "2026-11-02", "")

    for heading in _template_headings():
        assert heading in body, heading
    for question in range(1, 8):
        assert re.search(rf"^{question}\. \*\*", body, re.M), f"question {question} unanswered"
    assert "- feat: a\n- fix: b" in body
    assert "dispatch of cyberbeuk" in body
    assert "## 0.4.0 (2026-11-02)" in body
    assert "po:approved" in body


def test_no_release_body_waits_for_an_audit() -> None:
    """ADR 0128: the audit is optional, so neither a minor nor a patch body asks for a verdict."""
    for version, previous in (("0.4.0", "v0.3.0"), ("1.0.0", "v0.9.0"), ("0.4.1", "v0.4.0")):
        body = pr_body(version, previous, ["feat: a"], "po", "2026-11-02", "")

        assert not re.search(r"^## Release audit", body, re.M)
        assert "Verdict" not in body
        assert "optional and gates nothing (ADR 0128)" in body


def test_the_tag_message_records_the_instruction() -> None:
    assert tag_message("0.4.0", "cyberbeuk", "2026-11-02") == (
        "v0.4.0: tagged on the instruction of cyberbeuk on 2026-11-02"
    )


# Against a repository.


@pytest.fixture
def released(git: Git, tmp_path: Path) -> Path:
    """A repository at v0.3.0 with a feature and a candidate after it."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "uv.lock").write_text(LOCK)
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    (tmp_path / "publiccode.yml").write_text(PUBLICCODE)
    commit_all(git, "chore(release): v0.3.0 (#10)")
    git("tag", "-a", "v0.3.0", "-m", "v0.3.0")
    (tmp_path / "a.txt").write_text("a\n")
    commit_all(git, "feat: a")
    git("tag", "-a", "v0.4.0-rc.1", "-m", "candidate")
    return tmp_path


PUBLICCODE = """# comment kept
publiccodeYmlVersion: "0"
name: OpenPlanner
softwareVersion: "0.3.0"
releaseDate: "2026-10-30"
"""


def test_bump_publiccode_sets_version_and_date_and_keeps_the_rest() -> None:
    result = bump_publiccode(PUBLICCODE, "0.4.0", "2026-11-02")
    assert result == PUBLICCODE.replace("0.3.0", "0.4.0").replace("2026-10-30", "2026-11-02")


def test_bump_publiccode_refuses_a_file_without_the_two_lines() -> None:
    with pytest.raises(ValueError, match="softwareVersion"):
        bump_publiccode('name: OpenPlanner\nsoftwareVersion: "0.3.0"\n', "0.4.0", "2026-11-02")


def test_publiccode_says_the_version_of_pyproject() -> None:
    """Between releases both carry the last release; the release PR moves both at once."""
    pyproject = re.search(r'^version = "([^"]+)"$', (ROOT / "pyproject.toml").read_text(), re.M)
    publiccode = re.search(
        r'^softwareVersion: "([^"]+)"$', (ROOT / "publiccode.yml").read_text(), re.M
    )
    assert pyproject is not None
    assert publiccode is not None
    assert publiccode.group(1) == pyproject.group(1)


def _merge_release(git: Git, repo: Path, version: str, pyproject: str, number: int) -> str:
    (repo / "pyproject.toml").write_text(pyproject)
    (repo / "CHANGELOG.md").write_text(CHANGELOG.replace("## Unreleased", f"## {version}"))
    commit_all(git, f"chore(release): v{version} (#{number})")
    git("update-ref", f"refs/remotes/origin/{git('branch', '--show-current').strip()}", "HEAD")
    return git("rev-parse", "HEAD").strip()


def test_the_release_commit_is_the_squash_merged_release_pr(git: Git, released: Path) -> None:
    sha = _merge_release(git, released, "0.4.0", PYPROJECT.replace("0.3.0", "0.4.0"), 12)
    (released / "b.txt").write_text("b\n")
    commit_all(git, "fix: after the release")

    assert release_commit("0.4.0", "main") == (sha, "12")


def test_a_release_pr_that_is_not_merged_or_says_another_version_is_refused(
    git: Git, released: Path
) -> None:
    with pytest.raises(ValueError, match="no merged release PR"):
        release_commit("0.4.0", "main")
    _merge_release(git, released, "0.4.0", PYPROJECT, 12)  # pyproject still says 0.3.0
    with pytest.raises(ValueError, match="does not say 0.4.0"):
        release_commit("0.4.0", "main")


def test_plan_prints_outputs_for_the_workflow(
    released: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["plan"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "version=0.4.0",
        "previous=v0.3.0",
        "base=main",
        "branch=release/v0.4.0",
        "title=chore(release): v0.4.0",
    ]
    assert main(["plan", "--version", "0.3.1"]) == 0
    assert "version=0.3.1" in capsys.readouterr().out
    assert main(["plan", "--version", "0.3.0"]) == 1
    assert "::error::0.3.0 is not above the last release v0.3.0" in capsys.readouterr().err


def test_prepare_edits_the_four_files_and_writes_the_body(
    released: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = released / "body.md"

    code = main(
        [
            "prepare",
            "--version",
            "0.4.0",
            "--body",
            str(body),
            "--actor",
            "po",
            "--date",
            "2026-11-02",
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert 'version = "0.4.0"' in (released / "pyproject.toml").read_text()
    assert 'name = "openplanner"\nversion = "0.4.0"' in (released / "uv.lock").read_text()
    assert "## Unreleased\n\n## 0.4.0 (2026-11-02)" in (released / "CHANGELOG.md").read_text()
    publiccode = (released / "publiccode.yml").read_text()
    assert 'softwareVersion: "0.4.0"' in publiccode
    assert 'releaseDate: "2026-11-02"' in publiccode
    assert "- feat: a" in body.read_text()
    assert "Commits since v0.3.0 (1)" in body.read_text()


def test_tag_check_finds_the_release_and_refuses_an_existing_tag(
    git: Git, released: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    assert main(["tag-check", "--version", "0.4.0"]) == 1
    assert "no merged release PR" in capsys.readouterr().err

    sha = _merge_release(git, released, "0.4.0", PYPROJECT.replace("0.3.0", "0.4.0"), 12)

    args = ["--ref", "main", "--actor", "po", "--date", "2026-11-02"]

    assert main(["tag-check", "--version", "0.4.0", *args]) == 0
    assert capsys.readouterr().out == (
        f"commit={sha}\nnumber=12\nmessage=v0.4.0: tagged on the instruction of po on 2026-11-02\n"
    )

    git("tag", "-a", "v0.4.0", "-m", "v0.4.0")

    assert main(["tag-check", "--version", "0.4.0"]) == 1
    assert "exists already" in capsys.readouterr().err


def test_plan_refuses_a_patch_that_carries_a_migration() -> None:
    """A patch carries no migration (ADR 0116 point 4); a higher number is the way out."""
    assert plan("v0.4.0", ["fix: a"], None, migrations_changed=True) == "0.5.0"
    assert plan("v0.4.0", ["fix: a"], "0.5.0", migrations_changed=True) == "0.5.0"
    with pytest.raises(ValueError, match="carries no migration"):
        plan("v0.4.0", ["fix: a"], "0.4.1", migrations_changed=True)
    assert plan("v0.4.0", ["fix: a"], "0.4.1", migrations_changed=False) == "0.4.1"


def test_the_release_pr_lists_the_adrs_this_release_finishes() -> None:
    """ADR 0119: the product owner reads which decisions became built, and what is still open."""
    before = "\n".join(
        [
            "| 0001 | One | BUILT | e | – |",
            "| 0002 | Two | PARTIAL | e | x |",
            "| 0003 | Three | NOT BUILT | e | x |",
        ]
    )
    after = before.replace("| 0002 | Two | PARTIAL |", "| 0002 | Two | BUILT |") + (
        "\n| 0004 | Four | NOT BUILT (policy part) | e | x |"
    )

    section = adr_progress(before, after, "v0.5.0")

    assert section.startswith("## ADRs\n\nBuilt since v0.5.0 (1):\n\n- ADR 0002 Two\n")
    assert "Still open (2): 0003, 0004; of these nothing built yet: 0003, 0004." in section
    body = pr_body("0.6.0", "v0.5.0", ["feat: a"], "po", "2026-11-08", section)
    assert section in body
    assert "## ADRs" not in pr_body("0.6.0", "v0.5.0", ["feat: a"], "po", "2026-11-08", "")


# Maintenance lines (ADR 0116 point 3): the release PR and tag on a line, and the line's
# changelog taken over by the next release on main.


def test_the_release_plan_on_a_line_is_a_patch_of_it() -> None:
    line = Target("0.5")

    assert plan("v0.5.1", ["fix: a"], None, target=line) == "0.5.2"
    assert plan("v0.5.1", ["fix: a"], "0.5.3", target=line) == "0.5.3"
    with pytest.raises(LineError, match="not a patch of line 0.5"):
        plan("v0.5.1", ["fix: a"], "0.6.0", target=line)
    with pytest.raises(LineError, match="takes fixes only"):
        plan("v0.5.1", ["feat: a"], None, target=line)


def test_the_lines_sections_join_main_in_version_order() -> None:
    main_text = (
        "# Changelog\n\n## Unreleased\n\n## 0.6.0 (2026-11-20)\n\n- New.\n\n"
        "## 0.5.0 (x)\n\n- Base.\n"
    )
    line_text = (
        "# Changelog\n\n## Unreleased\n\n## 0.5.2 (y)\n\n- Fix 2.\n\n"
        "## 0.5.1 (z)\n\n- Fix 1.\n\n## 0.5.0 (x)\n\n- Other.\n"
    )

    merged = merge_changelogs(main_text, [line_text])

    assert merged == (
        "# Changelog\n\n## Unreleased\n\n## 0.6.0 (2026-11-20)\n\n- New.\n\n"
        "## 0.5.2 (y)\n\n- Fix 2.\n\n## 0.5.1 (z)\n\n- Fix 1.\n\n## 0.5.0 (x)\n\n- Base.\n"
    )
    assert merge_changelogs(merged, [line_text]) == merged
    assert merge_changelogs(CHANGELOG, []) == CHANGELOG


def test_the_release_pr_and_tag_on_a_line(
    git: Git, released: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cut_line(git, released, "0.3", "v0.3.0")

    assert main(["plan", "--base", "release/0.3"]) == 0
    out = capsys.readouterr().out
    assert "version=0.3.1" in out
    assert "base=release/0.3" in out

    _merge_release(git, released, "0.3.1", PYPROJECT.replace("0.3.0", "0.3.1"), 23)
    args = ["--ref", "release/0.3", "--actor", "po", "--date", "2026-11-09"]

    assert main(["tag-check", "--version", "0.3.1", *args]) == 0
    assert "number=23" in capsys.readouterr().out
    assert main(["tag-check", "--version", "0.4.1", *args]) == 1
    assert "not a patch of line 0.3" in capsys.readouterr().err


def test_a_dry_run_from_a_feature_branch_plans_and_tags_as_main(
    git: Git, released: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sha = _merge_release(git, released, "0.4.0", PYPROJECT.replace("0.3.0", "0.4.0"), 12)
    git("update-ref", "refs/remotes/origin/main", sha)

    assert main(["tag-check", "--version", "0.4.0", "--ref", "feat/try-the-workflow"]) == 0
    assert f"commit={sha}" in capsys.readouterr().out


def test_the_next_release_on_main_takes_over_the_lines_changelog(
    git: Git, released: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cut_line(git, released, "0.3", "v0.3.0")
    (released / "CHANGELOG.md").write_text(
        CHANGELOG.replace("## 0.3.0", "## 0.3.1 (2026-11-09)\n\n- The security fix.\n\n## 0.3.0")
    )
    commit_all(git, "chore(release): v0.3.1 (#23)")
    git("update-ref", "refs/remotes/origin/release/0.3", "HEAD")
    git("switch", "-q", "main")
    body = released / "body.md"

    code = main(["prepare", "--version", "0.4.0", "--body", str(body), "--date", "2026-11-20"])

    assert code == 0, capsys.readouterr().err
    changelog = (released / "CHANGELOG.md").read_text()
    assert "- A thing.\n\n## 0.3.1 (2026-11-09)\n\n- The security fix.\n\n## 0.3.0" in changelog
    assert changelog.index("## 0.3.1") < changelog.index("## 0.3.0")
