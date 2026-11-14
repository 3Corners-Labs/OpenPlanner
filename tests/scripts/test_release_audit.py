"""The optional release audit runs Codex against the last release, and nothing waits for it (ADR 0128)."""

import os
import subprocess
from pathlib import Path

import pytest

from tests.scripts.conftest import Git, commit_all, fake_program, script_path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "release-audit.sh"
WORKFLOWS = ROOT / ".github" / "workflows"


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


def test_no_workflow_runs_the_audit_or_waits_for_it() -> None:
    """ADR 0128: no job needs a second model's account, and no tag waits for a verdict."""
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        text = workflow.read_text()
        for word in ("release-audit", "codex", "OPENAI_API_KEY", "Verdict"):
            assert word not in text, f"{workflow.name} still refers to {word}"
    assert not (ROOT / "scripts" / "check-release-audit.sh").exists()
