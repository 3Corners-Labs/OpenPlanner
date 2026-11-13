"""The manifest of a vendored front-end file: its one shape and its one reader (ADR 0118 point 6).

A vendored file lives under ``app/web/static/vendor/<name>/`` next to a ``manifest.json``::

    {
      "name": "webex",
      "version": "3.13.0",
      "source": "npm package webex, umd/meetings.min.js",
      "file": "meetings.min.js",
      "checksum": {"version": "3.13.0", "sha256": "<64 hex characters>"}
    }

``checksum`` is absent until the file is placed (ADR 0123 point 3). Renovate moves ``version``
only, so a checksum whose own ``version`` differs belongs to an older file: ``checksum_is_current``
is false until the file and the checksum are replaced together. ``scripts.check_pins``,
``scripts.vendor_sbom`` and ``tests/web/test_vendor.py`` read manifests through this module only.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIRECTORY = "app/web/static/vendor"
MANIFEST = "manifest.json"


@dataclass(frozen=True)
class Manifest:
    """One vendored file's manifest.

    Attributes:
        path: The manifest file.
        name: The package's name (the npm package for a Renovate datasource).
        version: The version pinned; the only field Renovate changes.
        source: Where the file comes from, in words.
        file: The vendored file's name next to the manifest.
        checksum_version: The version the checksum was taken of; ``None`` until placed.
        sha256: The file's SHA-256 in hex; ``None`` until placed.
    """

    path: Path
    name: str
    version: str
    source: str
    file: str
    checksum_version: str | None
    sha256: str | None

    @property
    def vendored(self) -> Path:
        """The vendored file's path (it may not exist: it is placed, not committed)."""
        return self.path.parent / self.file

    @property
    def placed(self) -> bool:
        """Whether the vendored file is next to its manifest."""
        return self.vendored.is_file()

    @property
    def checksum_is_current(self) -> bool:
        """Whether the manifest carries a checksum taken of the pinned ``version``."""
        return self.sha256 is not None and self.checksum_version == self.version


def file_matches(manifest: Manifest) -> bool:
    """Whether the placed file's bytes are the ones the manifest's current checksum names.

    True only when the file is placed, the checksum is of the pinned version and the SHA-256 of
    the file equals it. The one place that hashes a vendored file: the size and checksum gates
    of ``tests/web/test_vendor.py`` and the SBOM step both use it.
    """
    return (
        manifest.placed
        and manifest.checksum_is_current
        and hashlib.sha256(manifest.vendored.read_bytes()).hexdigest() == manifest.sha256
    )


def load(path: Path) -> Manifest:
    """Read the manifest at ``path``.

    Raises:
        KeyError: A required field is missing.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    checksum = data.get("checksum") or {}
    return Manifest(
        path=path,
        name=data["name"],
        version=data["version"],
        source=data["source"],
        file=data["file"],
        checksum_version=checksum.get("version"),
        sha256=checksum.get("sha256"),
    )


def manifests(root: Path = ROOT) -> list[Manifest]:
    """Return every vendored manifest under ``root``, in path order."""
    return [load(path) for path in sorted((root / VENDOR_DIRECTORY).glob(f"*/{MANIFEST}"))]
