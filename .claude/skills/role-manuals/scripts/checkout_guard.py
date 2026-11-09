"""Keep an organisation's workspace outside every checkout of the software.

The Python counterpart of ``workspace.mjs``: ``check.py`` and ``release.py`` call it before they
read or write a workspace, so an organisation's words, screenshots and evidence never land in a
repository.
"""

import sys
from pathlib import Path


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
