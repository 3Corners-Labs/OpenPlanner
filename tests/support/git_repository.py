"""A throw-away git repository per test, for the scripts and skill scripts that run against git.

The ``git`` fixture in ``tests/conftest.py`` makes one; tests stage and commit with
``commit_all``.
"""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

GIT = shutil.which("git") or "/usr/bin/git"
Git = Callable[..., str]
"""Runs git in the test repository with the given arguments and returns its output."""


def repository(path: Path, monkeypatch: pytest.MonkeyPatch) -> Git:
    """Make ``path`` a git repository on branch ``main`` and the working directory."""
    monkeypatch.chdir(path)
    for variable in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{variable}_NAME", "test")
        monkeypatch.setenv(f"{variable}_EMAIL", "test@example.org")

    def run(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [GIT, *args], check=True, capture_output=True, text=True, cwd=path
        ).stdout

    run("init", "-q", "-b", "main")
    return run


def commit_all(git: Git, message: str) -> None:
    """Stage everything and commit it."""
    git("add", "-A")
    git("commit", "-q", "-m", message)
