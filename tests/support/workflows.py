"""The GitHub workflow files in tests: the events that start one, and one step, read and run."""

import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


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


def workflow_step(workflow: Path, job: str, step_id: str) -> dict[str, Any]:
    """Return the step with ``id: step_id`` of ``job`` in a GitHub workflow file."""
    steps = yaml.safe_load(workflow.read_text())["jobs"][job]["steps"]
    return next(step for step in steps if step.get("id") == step_id)


def run_step(script: str, env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a workflow step's ``run:`` script with bash and only ``env``, as the runner does."""
    return subprocess.run(  # noqa: S603 - a workflow's own step, with the test's fakes on PATH
        [shutil.which("bash") or "/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=dict(env),
    )
