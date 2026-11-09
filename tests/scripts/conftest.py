"""Helpers for the scripts that run against git: fake programs and a release line.

The throw-away repository itself is the ``git`` fixture of ``tests/conftest.py``; ``Git`` and
``commit_all`` come from ``tests/support/git_repository.py``.
"""

import os
from pathlib import Path

from tests.support.git_repository import Git, commit_all


def fake_program(directory: Path, name: str, script: str) -> Path:
    """Put an executable shell script called ``name`` in ``directory`` and return the directory."""
    path = directory / name
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return directory


def script_path(fakes: Path) -> str:
    """A PATH with the fakes first and only the system's own tools after them."""
    return os.pathsep.join([str(fakes), "/usr/bin", "/bin"])


def cut_line(git: Git, root: Path, line: str, tag: str) -> None:
    """Cut ``release/<line>`` from ``tag`` with one fix on it, as on ``origin`` (ADR 0116).

    ``origin/main`` is the current ``main`` and ``origin/release/<line>`` the line's head.
    """
    git("update-ref", "refs/remotes/origin/main", "main")
    git("switch", "-q", "-c", f"release/{line}", tag)
    (root / "fix.txt").write_text("fix\n")
    commit_all(git, "fix: the security fix (#21)")
    git("update-ref", f"refs/remotes/origin/release/{line}", "HEAD")
