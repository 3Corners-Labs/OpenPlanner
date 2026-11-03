"""The evaluation launcher (docs/try-it.md), run against a fake docker."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
BASH = shutil.which("bash") or "/bin/bash"
FAKE_DOCKER = """#!/bin/sh
echo "docker $*" >> "$FAKE_LOG"
case "$1 $2" in
  "compose version") echo "${FAKE_COMPOSE:-2.40.0}"; exit 0 ;;
esac
[ "$1" = info ] && exit "${FAKE_INFO_RC:-0}"
exit 0
"""


def _run(tmp_path: Path, *args: str, **env: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run try.sh in a copy of the repository files it needs, with a fake docker first on PATH."""
    work = tmp_path / "repo"
    (work / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "try.sh", work / "scripts" / "try.sh")
    shutil.copy(REPO / ".env.example", work / ".env.example")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log"
    log.touch()
    full_env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "READY_TIMEOUT": "0",
        **env,
    }
    result = subprocess.run(  # noqa: S603
        [BASH, str(work / "scripts" / "try.sh"), *args],
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )
    return result, log.read_text()


def test_it_is_valid_shell_and_powershell_names_the_same_commands() -> None:
    syntax = subprocess.run(  # noqa: S603
        [BASH, "-n", str(REPO / "scripts/try.sh")], check=False
    )
    assert syntax.returncode == 0
    ps1 = (REPO / "scripts/try.ps1").read_text()
    for word in ("up", "status", "logs", "down", "reset", "seed-demo", "readyz", "2.24.0"):
        assert word in ps1


def _tools_without_docker(tmp_path: Path) -> Path:
    """Link the real tools the script needs into a directory that has no docker."""
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("dirname", "sort", "head", "grep", "sed", "cat"):
        found = shutil.which(name)
        assert found
        (tools / name).symlink_to(found)
    return tools


def test_a_missing_docker_is_explained(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    (work / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "try.sh", work / "scripts" / "try.sh")
    result = subprocess.run(  # noqa: S603
        [BASH, str(work / "scripts" / "try.sh"), "down"],
        capture_output=True,
        text=True,
        env={"PATH": str(_tools_without_docker(tmp_path))},
        check=False,
    )
    assert result.returncode == 1
    assert "Docker is not installed" in result.stderr


def test_a_stopped_docker_is_explained(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, "down", FAKE_INFO_RC="1")
    assert result.returncode == 1
    assert "not running" in result.stderr


def test_an_old_compose_is_refused(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, "down", FAKE_COMPOSE="2.20.1")
    assert result.returncode == 1
    assert "too old" in result.stderr


def test_down_and_reset_stop_the_dev_stack(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "down")
    assert result.returncode == 0
    assert "docker compose --profile dev down" in log
    result, log = _run(tmp_path / "again", "reset")
    assert "down --volumes" in log


def test_an_unknown_command_fails(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, "bogus")
    assert result.returncode == 1
    assert "unknown command" in result.stderr


@pytest.mark.parametrize("args", [("up", "--no-open"), ("--no-open",)])
def test_up_writes_env_and_builds(tmp_path: Path, args: tuple[str, ...]) -> None:
    result, log = _run(tmp_path, *args)
    assert (tmp_path / "repo" / ".env").exists() or "in use" in result.stderr
    if "in use" in result.stderr:
        pytest.skip("a local port is taken on this machine")
    assert "up --build -d" in log
    assert result.returncode == 0 or "did not become ready" in result.stderr
