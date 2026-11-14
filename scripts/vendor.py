"""Where the repository keeps its vendored front-end files, and a walk over their manifests.

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
is false until the file and the checksum are replaced together. The shape and its reader
are ``app.adapters.vendored_files`` (the server uses them too); this module only finds the
manifests of the repository, for the scripts that read them.
"""

from pathlib import Path

from app.adapters.vendored_files import MANIFEST, Manifest, load

ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIRECTORY = "app/web/static/vendor"


def manifests(root: Path = ROOT) -> list[Manifest]:
    """Return every vendored manifest under ``root``, in path order."""
    return [load(path) for path in sorted((root / VENDOR_DIRECTORY).glob(f"*/{MANIFEST}"))]
