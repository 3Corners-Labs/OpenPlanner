"""The ``shellcheck`` target of the ``Makefile``: the host scripts and the scripts skills ship.

CI runs ``make shellcheck`` on every pull request, so this target decides which shell scripts are
linted. A repo skill keeps its helpers in ``.claude/skills/<skill>/scripts/``. While no skill
ships a shell script the target must still run; a file name must reach shellcheck as one
argument, never as shell code; and a dangling link must reach shellcheck too, which then fails,
instead of dropping the other skill scripts. A fake ``uvx`` records the arguments instead of
running shellcheck.
"""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKE = shutil.which("make") or "/usr/bin/make"
HOST_SCRIPTS = ["scripts/a.sh", "scripts/updater/b.sh", "deploy/proxy/c.sh", ".githooks/pre-push"]
"""One file for each host pattern of the target, in the order the target lists them."""


def _shellcheck(
    tmp_path: Path, *files: str, exit_code: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run ``make shellcheck`` in a tree of the host scripts and ``files``, with a fake ``uvx``.

    Returns:
        Make's result and the files the fake shellcheck was given, in order.
    """
    tree, bin_dir, argv = tmp_path / "tree", tmp_path / "bin", tmp_path / "argv"
    for name in [*HOST_SCRIPTS, *files]:
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text("#!/bin/sh\n", encoding="utf-8")
    bin_dir.mkdir()
    uvx = bin_dir / "uvx"
    uvx.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{argv}'\nexit {exit_code}\n")
    uvx.chmod(0o755)
    # The make running these tests passes its flags down; this make starts clean.
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("MAKE", "MFLAGS"))
    }
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [MAKE, "-s", "-C", str(tree), "-f", str(ROOT / "Makefile"), "shellcheck"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    # uvx --from shellcheck-py==<version> shellcheck <file>...
    recorded = argv.read_text(encoding="utf-8").splitlines() if argv.exists() else []
    return done, recorded[3:]


def test_the_scripts_that_skills_ship_are_linted(tmp_path: Path) -> None:
    done, linted = _shellcheck(
        tmp_path,
        ".claude/skills/demo/SKILL.md",
        ".claude/skills/demo/scripts/journey.mjs",
        ".claude/skills/demo/scripts/stack.sh",
        ".claude/skills/other/scripts/run.sh",
    )
    assert done.returncode == 0, done.stderr
    assert linted == [
        *HOST_SCRIPTS,
        ".claude/skills/demo/scripts/stack.sh",
        ".claude/skills/other/scripts/run.sh",
    ]


def test_the_target_runs_while_no_skill_ships_a_script(tmp_path: Path) -> None:
    done, linted = _shellcheck(tmp_path, ".claude/skills/review/SKILL.md")
    assert done.returncode == 0, done.stderr
    assert linted == HOST_SCRIPTS


def test_a_dangling_link_reaches_shellcheck_with_the_rest(tmp_path: Path) -> None:
    link = tmp_path / "tree" / ".claude/skills/aaa/scripts/gone.sh"
    link.parent.mkdir(parents=True)
    link.symlink_to("missing.sh")
    done, linted = _shellcheck(tmp_path, ".claude/skills/demo/scripts/stack.sh")
    assert done.returncode == 0, done.stderr
    assert linted == [
        *HOST_SCRIPTS,
        ".claude/skills/aaa/scripts/gone.sh",
        ".claude/skills/demo/scripts/stack.sh",
    ]


def test_a_skill_script_name_is_never_run_as_code(tmp_path: Path) -> None:
    name = ".claude/skills/demo/scripts/x;touch ran;#.sh"
    done, linted = _shellcheck(tmp_path, name)
    assert done.returncode == 0, done.stderr
    assert linted == [*HOST_SCRIPTS, name]
    assert not (tmp_path / "tree" / "ran").exists()


def test_a_shellcheck_failure_fails_the_target(tmp_path: Path) -> None:
    done, _ = _shellcheck(tmp_path, ".claude/skills/demo/scripts/stack.sh", exit_code=1)
    assert done.returncode != 0
