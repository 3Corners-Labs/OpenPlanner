"""Fail when a pinned version in CI or the Makefile is one Renovate cannot update (ADR 0051).

Renovate keeps every dependency current, but only the versions it can see. Native managers
cover ``uses:``, ``container:``, ``image:`` and ``runs-on:`` in workflows, Dockerfiles, compose,
``pyproject.toml``/``uv.lock`` and ``package.json``. Any other version literal (an env var, a
``docker run`` tag, an ``npx tool@x.y`` call) needs a ``# renovate: datasource=… depName=…``
line directly above it, read by the custom manager in ``renovate.json``.

Rules checked:

1. Every ``uses:`` of a remote action is pinned to a full commit SHA with a ``# vX.Y.Z``
   comment, so Renovate can move both and a tag cannot be repointed underneath us.
2. Every other line with a version literal is covered by a native manager or annotated.
   A minimum version or protocol value that must not follow the latest release instead has a
   ``# not a pin: <reason>`` line above it.

Usage: ``python -m scripts.check_pins`` (``make pins``).
"""

import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Lines whose version Renovate reads natively (github-actions and docker managers), and step
# names, which are prose ("WCAG 2.2 AA").
NATIVE = re.compile(r"^\s*(-\s+)?(uses|container|image|runs-on|name):")
USES = re.compile(r"^\s*(-\s+)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^\s*#\s*v?\d+(\.\d+)*\S*\s*$")
# x.y or x.y.z, optionally v-prefixed or suffixed; not part of an IP address, a longer dotted
# run or a name such as the SPDX license ``Apache-2.0``.
VERSION = re.compile(r"(?<![\w.-])v?\d+\.\d+(?:\.\d+)?(?:-[\w.]+)?(?![\w.])")
ANNOTATION = re.compile(r"^\s*#\s*renovate:\s*datasource=\S+\s+depName=\S+")
# In shell scripts only a real pin counts (an assignment, an image or package reference, ``==``),
# not a version quoted in a message such as ``die "--version must look like 1.2.3"``.
SHELL_PIN = re.compile(r"(?:^\s*(?:export\s+)?\w+=[\"']?v?|[\w.-]/[\w./-]+[:@]v?|==)\d+\.\d+")
# A floor or a protocol value, not a pin: says so and why (``# not a pin: Compose 2.24 adds …``).
NOT_A_PIN = re.compile(r"^\s*#\s*not a pin:\s*\S")


@dataclass(frozen=True)
class Violation:
    """One untracked pin: where it is and what is wrong."""

    path: str
    line: int
    reason: str

    def __str__(self) -> str:
        """Render as ``path:line: reason`` for CI annotations and terminals."""
        return f"{self.path}:{self.line}: {self.reason}"


def check_lines(path: str, lines: Iterable[str]) -> Iterator[Violation]:
    """Yield the violations of both rules in one file's lines."""
    pin = SHELL_PIN if path.endswith(".sh") or "/.githooks/" in f"/{path}" else VERSION
    previous = ""
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if stripped:
                previous = stripped
            continue
        uses = USES.match(line)
        if uses:
            ref = uses["ref"]
            if not ref.startswith(("./", "docker://")) and not (
                PINNED.match(ref) and VERSION_COMMENT.match(uses["rest"])
            ):
                yield Violation(path, number, f"action {ref} is not pinned as <sha> # vX.Y.Z")
        elif (
            not NATIVE.match(line)
            and pin.search(_without_comment(line))
            and not ANNOTATION.match(previous)
            and not NOT_A_PIN.match(previous)
        ):
            yield Violation(path, number, "version without a `# renovate:` line above it")
        previous = stripped


def _without_comment(line: str) -> str:
    """Drop a trailing `` # comment`` so versions mentioned in prose do not count."""
    return line.split(" #", 1)[0]


def files(root: Path) -> list[Path]:
    """Return the files whose versions only the custom manager can see.

    Workflows, the Makefile and every shell script, the host installer and updater among them.
    """
    return sorted(
        [
            *(root / ".github" / "workflows").glob("*.y*ml"),
            root / "Makefile",
            *(root / "scripts").rglob("*.sh"),
            *(root / ".githooks").glob("*"),
        ]
    )


def main() -> int:
    """Check every file, print each violation and return the process exit code."""
    violations = [
        violation
        for path in files(ROOT)
        for violation in check_lines(
            str(path.relative_to(ROOT)), path.read_text(encoding="utf-8").splitlines()
        )
    ]
    for violation in violations:
        print(violation)
    if violations:
        print(f"{len(violations)} pin(s) Renovate cannot update; see docs/adr/0051.")
        return 1
    print("Every pinned version is tracked by Renovate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
