"""The manifests of the vendored front-end files of this repository (ADR 0118 point 6).

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

from pathlib import Path

from app.adapters.vendored_files import MANIFEST, Manifest, file_matches, load

__all__ = ["MANIFEST", "ROOT", "VENDOR_DIRECTORY", "Manifest", "file_matches", "load", "manifests"]

ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIRECTORY = "app/web/static/vendor"


def manifests(root: Path = ROOT) -> list[Manifest]:
    """Return every vendored manifest under ``root``, in path order."""
    return [load(path) for path in sorted((root / VENDOR_DIRECTORY).glob(f"*/{MANIFEST}"))]
