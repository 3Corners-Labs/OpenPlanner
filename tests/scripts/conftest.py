"""Fixtures for the scripts that run against git: a throw-away repository per test."""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

GIT = shutil.which("git") or "/usr/bin/git"
Git = Callable[..., str]
"""Runs git in the test repository with the given arguments and returns its output."""


@pytest.fixture
def git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Git:
    """A git repository on branch ``main`` in ``tmp_path``, which is the working directory."""
    monkeypatch.chdir(tmp_path)
    for variable in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{variable}_NAME", "test")
        monkeypatch.setenv(f"{variable}_EMAIL", "test@example.org")

    def run(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [GIT, *args], check=True, capture_output=True, text=True, cwd=tmp_path
        ).stdout

    run("init", "-q", "-b", "main")
    return run


def commit_all(git: Git, message: str) -> None:
    """Stage everything and commit it."""
    git("add", "-A")
    git("commit", "-q", "-m", message)


def fake_program(directory: Path, name: str, script: str) -> Path:
    """Put an executable shell script called ``name`` in ``directory`` and return the directory."""
    path = directory / name
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return directory


def script_path(fakes: Path) -> str:
    """A PATH with the fakes first and only the system's own tools after them."""
    return os.pathsep.join([str(fakes), "/usr/bin", "/bin"])
