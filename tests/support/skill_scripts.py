"""The scripts of the repo skills (``.claude/skills/``), loaded, copied or run for their tests.

They run on a developer's machine, outside the product. Their tests load a Python script as a
module, or copy a script into a throw-away repository (the ``git`` fixture) and run it there.
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

from tests.support.git_repository import Git, commit_all

SKILLS = Path(__file__).resolve().parents[2] / ".claude" / "skills"
BASH = shutil.which("bash") or "/bin/bash"
STACK = "verify-openplanner/scripts/stack.sh"


def load_skill_script(relative: str) -> ModuleType:
    """Import the Python script at ``.claude/skills/<relative>`` as a module of its own."""
    path = SKILLS / relative
    spec = importlib.util.spec_from_file_location(f"skills.{relative}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_skill_file(relative: str, root: Path) -> Path:
    """Copy ``.claude/skills/<relative>`` to the same place below ``root``; return the copy."""
    target = root / ".claude" / "skills" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SKILLS / relative, target)
    return target


def image_checkout(git: Git, root: Path) -> Path:
    """Commit ``stack.sh`` and an image that takes ``app/`` only to the repository at ``root``."""
    copy_skill_file(STACK, root)
    (root / ".dockerignore").write_text("*\n!app/\n")
    (root / "Dockerfile").write_text("FROM scratch\nCOPY app /app\n")
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("VALUE = 1\n")
    commit_all(git, "image")
    return root


def fingerprint(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``stack.sh fingerprint`` with ``args`` from its copy below ``root``."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [BASH, str(root / ".claude" / "skills" / STACK), "fingerprint", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
