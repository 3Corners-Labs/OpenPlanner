"""scripts/deploy-site.sh pushes the built site to the web host's repository (ADR 0087)."""

import os
import subprocess
from pathlib import Path

import pytest

from tests.scripts.conftest import Git

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy-site.sh"


def _deploy(
    dist: Path, env: dict[str, str], label: str = "v1 abc"
) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("SITE_DEPLOY_")}
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["/bin/bash", str(SCRIPT), str(dist), label],
        env={**base, "GITHUB_SHA": "0123456789ab", **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _files(git: Git, remote: Path, branch: str = "main") -> list[str]:
    return sorted(git("--git-dir", str(remote), "ls-tree", "-r", "--name-only", branch).split())


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    site = tmp_path / "dist"
    (site / "static").mkdir(parents=True)
    (site / "index.html").write_text("<h1>nl</h1>")
    (site / "static" / "site.css").write_text("body{}")
    return site


@pytest.fixture
def remote(git: Git, tmp_path: Path) -> Path:
    path = tmp_path / "auto.git"
    git("init", "-q", "--bare", "-b", "main", str(path))
    return path


def _env(remote: Path, **extra: str) -> dict[str, str]:
    return {
        "SITE_DEPLOY_REMOTE": str(remote),
        "SITE_DEPLOY_KEY": "unused for a local path",
        "SITE_DEPLOY_KNOWN_HOSTS": "x",
        **extra,
    }


def test_a_key_file_works_instead_of_the_key(
    git: Git, dist: Path, remote: Path, tmp_path: Path
) -> None:
    env = _env(remote, SITE_DEPLOY_KEY="", SITE_DEPLOY_KEY_FILE=str(tmp_path / "laptop-key"))
    result = _deploy(dist, env)
    assert result.returncode == 0, result.stderr
    assert "index.html" in _files(git, remote)


def test_without_remote_or_key_it_builds_and_does_not_deploy(dist: Path) -> None:
    result = _deploy(dist, {})
    assert result.returncode == 0
    assert "not deployed" in result.stdout


def test_first_push_fills_an_empty_repository_and_a_second_changes_nothing(
    git: Git, dist: Path, remote: Path
) -> None:
    first = _deploy(dist, _env(remote))
    assert first.returncode == 0, first.stderr
    assert _files(git, remote) == [".autogit.yml", "index.html", "static/site.css"]
    assert "site: v1 abc" in git("--git-dir", str(remote), "log", "-1", "--format=%s", "main")
    again = _deploy(dist, _env(remote), label="v2 def")
    assert again.returncode == 0, again.stderr
    assert "already this build" in again.stdout


def test_a_removed_page_disappears_from_the_host(git: Git, dist: Path, remote: Path) -> None:
    assert _deploy(dist, _env(remote)).returncode == 0
    (dist / "static" / "site.css").unlink()
    assert _deploy(dist, _env(remote), label="v2").returncode == 0
    assert _files(git, remote) == [".autogit.yml", "index.html"]


def test_the_site_can_live_in_a_folder_of_the_repository(
    git: Git, dist: Path, remote: Path
) -> None:
    result = _deploy(dist, _env(remote, SITE_DEPLOY_DIR="www"))
    assert result.returncode == 0, result.stderr
    assert _files(git, remote) == [".autogit.yml", "www/index.html", "www/static/site.css"]


@pytest.mark.parametrize("folder", ["../outside", "/var/www", "www/../.."])
def test_a_folder_outside_the_repository_is_refused(dist: Path, remote: Path, folder: str) -> None:
    result = _deploy(dist, _env(remote, SITE_DEPLOY_DIR=folder))
    assert result.returncode == 1
    assert "SITE_DEPLOY_DIR" in result.stdout


def test_an_empty_build_is_never_pushed(tmp_path: Path, remote: Path) -> None:
    (tmp_path / "empty").mkdir()
    result = _deploy(tmp_path / "empty", _env(remote))
    assert result.returncode == 1
    assert "index.html missing" in result.stdout
