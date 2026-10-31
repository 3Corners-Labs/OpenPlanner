"""The release audit runs Codex against the last release, and a minor tag without one is refused."""

import os
import subprocess
from pathlib import Path

import pytest

from tests.scripts.conftest import Git, commit_all, fake_program, script_path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "release-audit.sh"
CHECK = ROOT / "scripts" / "check-release-audit.sh"


def run(script: Path, fakes: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run a script with the fakes first on its PATH."""
    return subprocess.run(  # noqa: S603 - the repository's own script, fake codex and gh
        [str(script)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": script_path(fakes), **env},
    )


@pytest.fixture
def released(git: Git, tmp_path: Path) -> Path:
    """A repository with release v0.3.0, a candidate after it and more work on top."""
    (tmp_path / "a.txt").write_text("a\n")
    commit_all(git, "feat: a")
    git("tag", "-a", "v0.3.0", "-m", "v0.3.0")
    (tmp_path / "b.txt").write_text("b\n")
    commit_all(git, "feat: b")
    git("tag", "-a", "v0.4.0-rc.1", "-m", "candidate")
    (tmp_path / "c.txt").write_text("c\n")
    commit_all(git, "chore(release): v0.4.0 (#12)")
    return tmp_path / "fakes"


def test_the_audit_reviews_against_the_last_release_not_a_candidate(released: Path) -> None:
    released.mkdir()
    fake_program(released, "codex", 'printf "%s\\n" "$@" > "$FAKE_LOG"')
    log = released / "log"

    result = run(AUDIT, released, FAKE_LOG=str(log))

    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[:3] == ["review", "--base", "v0.3.0"]
    assert 'sandbox_mode="read-only"' in log.read_text()


def test_the_audit_says_when_codex_is_missing(released: Path) -> None:
    released.mkdir()

    result = run(AUDIT, released)

    assert result.returncode == 2
    assert "Codex CLI is not installed" in result.stderr


AUDITED = "## What\n\nv0.4.0\n\n## Release audit\n\nNo P1 findings.\n\nVerdict: pass\n"


@pytest.mark.parametrize(
    ("tag", "body", "code", "message"),
    [
        ("v0.4.1", "", 0, "patch release"),
        ("v0.4.0", AUDITED, 0, "release audit found in PR #12"),
        ("v0.4.0", "## What\n\nv0.4.0\n", 1, "no '## Release audit' section"),
        ("v0.4.0", AUDITED.replace("Verdict: pass", "Verdict: open"), 1, "Verdict: pass"),
        ("v1.0.0", AUDITED, 0, "release audit found"),
    ],
)
def test_a_minor_or_major_tag_needs_the_audit_in_its_release_pr(
    released: Path, tag: str, body: str, code: int, message: str
) -> None:
    released.mkdir()
    fake_program(released, "gh", 'printf "%s" "$FAKE_BODY"')

    result = run(CHECK, released, GITHUB_REF_NAME=tag, FAKE_BODY=body)

    assert result.returncode == code, result.stdout + result.stderr
    assert message in result.stdout


def test_a_tag_on_a_commit_that_is_no_release_pr_is_refused(git: Git, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a\n")
    commit_all(git, "feat: not a release PR")
    fakes = tmp_path / "fakes"
    fakes.mkdir()

    result = run(CHECK, fakes, GITHUB_REF_NAME="v0.4.0")

    assert result.returncode == 1
    assert "not a squash-merged release PR" in result.stdout
