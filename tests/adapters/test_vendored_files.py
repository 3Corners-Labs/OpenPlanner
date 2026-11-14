"""A vendored file and whether it can be trusted (ADR 0123 point 3)."""

import base64
import hashlib
import json
import os
from pathlib import Path

from app.adapters.vendored_files import STATIC_VENDOR, FileTrust, VendoredFile


def _folder(
    root: Path,
    data: bytes | None,
    *,
    sha256: str | None = None,
    checksum_version: str | None = "1",
    manifest: bool = True,
) -> Path:
    """Write a manifest and, with ``data``, its file."""
    fields: dict[str, object] = {"name": "x", "version": "1", "source": "s", "file": "f.js"}
    if data is not None:
        (root / "f.js").write_bytes(data)
    if checksum_version is not None:
        fields["checksum"] = {
            "version": checksum_version,
            "sha256": sha256 or hashlib.sha256(data or b"").hexdigest(),
        }
    if manifest:
        (root / "manifest.json").write_text(json.dumps(fields))
    return root


def test_a_file_that_matches_its_current_checksum_is_trusted_and_offered_with_integrity(
    tmp_path: Path,
) -> None:
    file = VendoredFile(_folder(tmp_path, b"abc"))

    offer = file.offer()

    assert file.trust() is FileTrust.TRUSTED
    assert offer is not None
    assert offer.path == f"vendor/{tmp_path.name}/f.js"
    assert offer.integrity == "sha256-" + base64.b64encode(hashlib.sha256(b"abc").digest()).decode()


def test_a_file_that_differs_is_not_offered(tmp_path: Path) -> None:
    file = VendoredFile(_folder(tmp_path, b"abc", sha256="0" * 64))

    assert file.trust() is FileTrust.DIFFERS
    assert file.offer() is None


def test_a_checksum_of_another_version_or_none_is_not_enough(tmp_path: Path) -> None:
    assert VendoredFile(_folder(tmp_path, b"abc", checksum_version="0")).trust() is (
        FileTrust.NO_CHECKSUM
    )
    other = tmp_path / "other"
    other.mkdir()
    assert VendoredFile(_folder(other, b"abc", checksum_version=None)).trust() is (
        FileTrust.NO_CHECKSUM
    )


def test_no_file_and_no_manifest_are_not_placed(tmp_path: Path) -> None:
    assert VendoredFile(_folder(tmp_path, None)).trust() is FileTrust.NOT_PLACED
    missing = tmp_path / "missing"
    missing.mkdir()
    assert VendoredFile(missing).trust() is FileTrust.NOT_PLACED
    assert VendoredFile(tmp_path / "no-such-folder").offer() is None


def test_the_verdict_follows_the_file_without_a_restart(tmp_path: Path) -> None:
    file = VendoredFile(_folder(tmp_path, b"abc"))
    assert file.trust() is FileTrust.TRUSTED

    # Swapped for another file of the same size: the bytes differ, the verdict follows.
    path = tmp_path / "f.js"
    path.write_bytes(b"abd")
    os.utime(path, ns=(1, 1))

    assert file.trust() is FileTrust.DIFFERS
    assert file.offer() is None


def test_the_real_folder_of_the_application_is_read_as_it_is_laid_out() -> None:
    manifest = json.loads((STATIC_VENDOR / "webex" / "manifest.json").read_text())

    assert manifest["name"] == "webex"
    # Placed or not, the verdict is one of the four, never an error.
    assert isinstance(VendoredFile(STATIC_VENDOR / "webex").trust(), FileTrust)
