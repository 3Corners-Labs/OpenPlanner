"""An organisation's workspace of role manuals: where its files are, and never in a checkout.

The Python counterpart of ``workspace.mjs``: ``check.py`` and ``release.py`` open a workspace
only through ``Workspace``, which refuses one inside a checkout of the software, so an
organisation's words, screenshots and evidence never land in a repository.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

APPROACH = "00-"
RELEASE_NOTES = "90-"
"""The prefixes of the sources that belong to no role: the approach and the release notes."""


def is_support(stem: str) -> bool:
    """Return whether a source (by its file stem) is the approach or the release notes.

    They have no role of their own: no tasks, no rights, no matrix role.
    """
    return stem.startswith((APPROACH, RELEASE_NOTES))


def guard_outside_checkout(path: Path) -> None:
    """Stop when ``path`` lies inside a checkout of the software (same rule as workspace.mjs).

    Symbolic links are followed first, so a link into a checkout is refused as well.
    """
    path = path.resolve()
    for folder in (path, *path.parents):
        if (folder / "app" / "i18n" / "locales" / "nl.po").exists() or (
            folder / ".claude" / "skills" / "role-manuals"
        ).exists():
            sys.exit(f"{path} lies inside the checkout {folder}; keep the workspace outside it")


@dataclass
class Workspace:
    """The files of one organisation's manuals; it opens only outside a checkout."""

    root: Path
    variables: Path = field(init=False)
    sources: Path = field(init=False)
    img: Path = field(init=False)
    build: Path = field(init=False)
    evidence: Path = field(init=False)

    def __post_init__(self) -> None:
        """Refuse a workspace inside a checkout, then name its files below the root."""
        self.root = self.root.resolve()
        guard_outside_checkout(self.root)
        self.variables = self.root / "variables.json"
        self.sources = self.root / "sources"
        self.img = self.root / "img"
        self.build = self.root / "build"
        self.evidence = self.root / "evidence"
