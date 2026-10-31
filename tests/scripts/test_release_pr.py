"""The release workflow's file edits, PR body, audit section and tag checks (ADR 0079)."""

import re
from pathlib import Path

import pytest

from scripts.release_pr import (
    AUDIT_HEADING,
    VERDICT_OPEN,
    bump_lock,
    bump_publiccode,
    bump_pyproject,
    main,
    needs_audit,
    parse_version,
    plan,
    pr_body,
    release_changelog,
    release_commit,
    tag_message,
    with_audit,
)
from tests.scripts.conftest import Git, commit_all

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


def test_a_minor_or_major_release_needs_the_audit_a_patch_does_not() -> None:
    assert needs_audit("0.4.0")
    assert needs_audit("1.0.0")
    assert not needs_audit("0.4.1")


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
    body = pr_body("0.4.0", "v0.3.0", ["feat: a", "fix: b"], "cyberbeuk", "2026-11-02")

    for heading in _template_headings():
        assert heading in body, heading
    for question in range(1, 8):
        assert re.search(rf"^{question}\. \*\*", body, re.M), f"question {question} unanswered"
    assert "- feat: a\n- fix: b" in body
    assert "dispatch of cyberbeuk" in body
    assert "## 0.4.0 (2026-11-02)" in body
    assert "po:approved" in body


def test_a_minor_release_body_carries_an_open_audit_section_a_patch_does_not() -> None:
    minor = pr_body("0.4.0", "v0.3.0", ["feat: a"], "po", "2026-11-02")
    patch = pr_body("0.4.1", "v0.4.0", ["fix: a"], "po", "2026-11-02")

    assert AUDIT_HEADING in minor
    assert VERDICT_OPEN in minor
    assert "Verdict: pass" not in minor
    assert AUDIT_HEADING not in patch
    assert "Verdict" not in patch


def test_the_audit_section_is_added_or_replaced_and_ends_open() -> None:
    body = pr_body("0.4.0", "v0.3.0", ["feat: a"], "po", "2026-11-02")

    first = with_audit(body, "## Findings\n\nP1: a bug in x.py:3\nP2: a nit\n")

    assert first.count(AUDIT_HEADING) == 1
    assert "Pending:" not in first
    # Headings inside the findings would split the section; they become bold lines.
    assert "**Findings**" in first
    assert "## Findings" not in first
    section = first[first.index(AUDIT_HEADING) :]
    assert section.split("## How tested")[0].rstrip().endswith(VERDICT_OPEN)
    assert "## How tested" in first

    second = with_audit(first, "No findings.\n")

    assert "P1: a bug" not in second
    assert "No findings." in second
    assert second.count(AUDIT_HEADING) == 1
    assert second.count(VERDICT_OPEN) == 1


def test_the_audit_section_is_appended_when_the_body_has_none() -> None:
    appended = with_audit("## What\n\nv0.4.0\n", "")

    assert appended.endswith(
        f"{AUDIT_HEADING}\n\nThe audit produced no output.\n\n{VERDICT_OPEN}\n\n"
    )


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
        "branch=release/v0.4.0",
        "title=chore(release): v0.4.0",
        "audit=true",
    ]
    assert main(["plan", "--version", "0.3.1"]) == 0
    assert "audit=false" in capsys.readouterr().out
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


def test_audit_writes_the_findings_into_the_body_file(released: Path) -> None:
    body, findings = released / "body.md", released / "findings.md"
    body.write_text(pr_body("0.4.0", "v0.3.0", ["feat: a"], "po", "2026-11-02"))
    findings.write_text("P2: a nit\n")

    assert main(["audit", "--body", str(body), "--findings", str(findings)]) == 0

    assert "P2: a nit\n\nVerdict: open" in body.read_text()


def test_tag_check_finds_the_release_and_refuses_an_existing_tag(
    git: Git, released: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["tag-check", "--version", "0.4.0", "--branch", "main"]) == 1
    assert "no merged release PR" in capsys.readouterr().err

    sha = _merge_release(git, released, "0.4.0", PYPROJECT.replace("0.3.0", "0.4.0"), 12)

    args = ["--branch", "main", "--actor", "po", "--date", "2026-11-02"]

    assert main(["tag-check", "--version", "0.4.0", *args]) == 0
    assert capsys.readouterr().out == (
        f"commit={sha}\nnumber=12\nmessage=v0.4.0: tagged on the instruction of po on 2026-11-02\n"
    )

    git("tag", "-a", "v0.4.0", "-m", "v0.4.0")

    assert main(["tag-check", "--version", "0.4.0", "--branch", "main"]) == 1
    assert "exists already" in capsys.readouterr().err
