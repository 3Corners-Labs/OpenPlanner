"""A vendored front-end file, its manifest and whether the file can be trusted (ADR 0118, 0123).

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
is false until the file and the checksum are replaced together.

This module is the one reader of that shape, for the server and for the repository's scripts
(``scripts.vendor`` re-exports it). The server asks ``VendoredFile`` whether the call page may
offer the file: only a file that is placed, whose checksum is of the pinned version and whose
bytes equal it is offered, and then with its checksum as subresource integrity. Nothing here
reads the file's content for anything but its hash.
"""

import base64
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

MANIFEST = "manifest.json"
STATIC_VENDOR = Path(__file__).resolve().parent.parent / "web" / "static" / "vendor"
"""Where vendored files live in the application (the static folder of ``app.web``)."""


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
    the file equals it. The size and checksum gates of ``tests/web/test_vendor.py`` and the SBOM
    step use it, and ``VendoredFile`` builds on it.
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


class FileTrust(StrEnum):
    """Whether a vendored file may be used; the value is a stable code for reports."""

    TRUSTED = "trusted"
    NOT_PLACED = "not_placed"
    NO_CHECKSUM = "no_checksum"
    DIFFERS = "differs"


@dataclass(frozen=True)
class Offer:
    """A trusted vendored file as a page may refer to it.

    Attributes:
        path: The file's path inside the static folder (``vendor/webex/meetings.min.js``).
        integrity: The ``integrity`` attribute value (``sha256-<base64>``) of the file.
    """

    path: str
    integrity: str


class VendoredFile:
    """The vendored file of one folder, and whether its bytes are the ones the manifest names.

    The answer is recomputed only when the file's or the manifest's size or modification time
    changes, so a page request costs two ``stat`` calls and not a read of megabytes.

    Args:
        folder: The folder with ``manifest.json`` and the file.
    """

    def __init__(self, folder: Path) -> None:
        """Remember ``folder``."""
        self._folder = folder
        self._seen: tuple[tuple[int, ...], FileTrust, Offer | None] | None = None

    def _stamp(self) -> tuple[int, ...]:
        """Return the modification times and sizes of the manifest and the file (-1: absent)."""
        stamp: list[int] = []
        for name in (MANIFEST, self._file_name()):
            try:
                stat = (self._folder / name).stat()
            except OSError:
                stamp.extend((-1, -1))
            else:
                stamp.extend((stat.st_mtime_ns, stat.st_size))
        return tuple(stamp)

    def _file_name(self) -> str:
        """Return the manifest's file name; empty when there is no readable manifest."""
        try:
            return load(self._folder / MANIFEST).file
        except (OSError, KeyError, ValueError):
            return ""

    def _evaluate(self) -> tuple[FileTrust, Offer | None]:
        """Read the manifest and hash the file; the one place that does either."""
        try:
            manifest = load(self._folder / MANIFEST)
        except (OSError, KeyError, ValueError):
            return FileTrust.NOT_PLACED, None
        if not manifest.placed:
            return FileTrust.NOT_PLACED, None
        if not manifest.checksum_is_current or manifest.sha256 is None:
            return FileTrust.NO_CHECKSUM, None
        if not file_matches(manifest):
            return FileTrust.DIFFERS, None
        digest = base64.b64encode(bytes.fromhex(manifest.sha256)).decode("ascii")
        return FileTrust.TRUSTED, Offer(
            f"vendor/{self._folder.name}/{manifest.file}", f"sha256-{digest}"
        )

    def _current(self) -> tuple[FileTrust, Offer | None]:
        """Return the cached verdict, recomputed when a stamp moved."""
        stamp = self._stamp()
        if self._seen is None or self._seen[0] != stamp:
            trust, offer = self._evaluate()
            self._seen = (stamp, trust, offer)
        return self._seen[1], self._seen[2]

    def trust(self) -> FileTrust:
        """Return whether the file may be used and, when not, why."""
        return self._current()[0]

    def offer(self) -> Offer | None:
        """Return the file as a page may refer to it; ``None`` unless it is trusted."""
        return self._current()[1]
