"""Helpers for the scripts that run against git: fake programs and a release line.

The throw-away repository itself is the ``git`` fixture of ``tests/conftest.py``; ``Git`` and
``commit_all`` come from ``tests/support/git_repository.py``.
"""

import os
from pathlib import Path
from typing import Any

import yaml

from tests.support.git_repository import Git, commit_all


def fake_program(directory: Path, name: str, script: str) -> Path:
    """Put an executable shell script called ``name`` in ``directory`` and return the directory."""
    path = directory / name
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return directory


def workflow_events(workflow: Path) -> dict[str, Any]:
    """Return the events that start a GitHub workflow file, each with its settings.

    YAML 1.1 reads the key ``on`` as ``True``. The value is one event, a list or a mapping;
    an event given without settings maps to ``None``.
    """
    data = yaml.safe_load(workflow.read_text())
    on = data.get("on", data.get(True))
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return dict.fromkeys(on)
    return dict(on or {})


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
