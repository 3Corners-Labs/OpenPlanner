"""Shell scripts that stand in for a program on PATH, for the tests of the scripts that call one."""

import os
from pathlib import Path


def fake_program(directory: Path, name: str, script: str) -> Path:
    """Put an executable shell script called ``name`` in ``directory`` and return the directory."""
    path = directory / name
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return directory


def script_path(fakes: Path) -> str:
    """A PATH with the fakes first and only the system's own tools after them."""
    return os.pathsep.join([str(fakes), "/usr/bin", "/bin"])
