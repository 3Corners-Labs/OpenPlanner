"""The vendored SDK of the call page (ADR 0123 point 3): manifest, size gate and checksum.

The file itself is not committed until Cisco's written answer on the licence (point 18), so each
gate also runs against a file written by the test, and against the real one when an installation
has placed it.
"""

import gzip
import hashlib
import json
import os
import subprocess
from pathlib import Path

VENDOR = Path("app/web/static/vendor")
WEBEX = VENDOR / "webex"
MAX_GZIP_BYTES = 1_200 * 1024
"""The size gate of ADR 0123 point 3: growth past 1.2 MB gzipped is a decision, not a drift."""
FILE = "app/web/static/vendor/webex/meetings.min.js"


def problems(directory: Path) -> list[str]:
    """Return what is wrong with the vendored folder ``directory``; empty when it passes."""
    manifest = json.loads((directory / "manifest.json").read_text())
    found = [
        f"manifest has no {field}"
        for field in ("name", "version", "source", "file", "sha256", "sha256_of")
        if field not in manifest
    ]
    file = directory / manifest.get("file", "")
    if not file.is_file():
        return found  # not placed (ADR 0123 point 3): the gates have nothing to check
    data = file.read_bytes()
    if len(gzip.compress(data, mtime=0)) > MAX_GZIP_BYTES:
        found.append("file is over 1.2 MB gzipped")
    if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
        found.append("sha256 differs from the manifest")
    if manifest.get("sha256_of") != manifest.get("version"):
        # Renovate moves ``version`` only: until the file and its checksum are replaced too and
        # ``sha256_of`` names the new version, a placed file is the old one.
        found.append("sha256 was taken of another version")
    return found


def _placed(folder: Path, data: bytes, **changes: str) -> Path:
    """Write ``data`` as the vendored file with a manifest that matches it, less ``changes``."""
    (folder / "meetings.min.js").write_bytes(data)
    manifest = {
        "name": "x",
        "version": "1",
        "source": "s",
        "file": "meetings.min.js",
        "sha256": hashlib.sha256(data).hexdigest(),
        "sha256_of": "1",
        **changes,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def test_the_manifest_names_the_pinned_version_and_the_file() -> None:
    manifest = json.loads((WEBEX / "manifest.json").read_text())

    assert manifest["name"] == "webex"
    assert manifest["version"] == "3.13.0"
    assert manifest["file"] == "meetings.min.js"
    assert "umd/meetings.min.js" in manifest["source"]
    assert manifest["sha256"] == "" or len(manifest["sha256"]) == 64


def test_the_real_folder_passes_when_the_file_is_placed_and_has_nothing_to_check_when_not() -> None:
    assert problems(WEBEX) == []


def test_a_file_that_fits_and_matches_passes(tmp_path: Path) -> None:
    assert problems(_placed(tmp_path, b"console.log(1);" * 100)) == []


def test_a_file_that_differs_from_the_manifest_fails(tmp_path: Path) -> None:
    assert problems(_placed(tmp_path, b"swapped", sha256="0")) == [
        "sha256 differs from the manifest"
    ]


def test_a_placed_file_with_an_empty_checksum_fails(tmp_path: Path) -> None:
    assert problems(_placed(tmp_path, b"x", sha256="", sha256_of="")) == [
        "sha256 differs from the manifest",
        "sha256 was taken of another version",
    ]


def test_a_version_update_alone_fails_where_the_file_is_placed(tmp_path: Path) -> None:
    # What a Renovate pull request does: it moves ``version`` and nothing else.
    assert problems(_placed(tmp_path, b"old file", version="2")) == [
        "sha256 was taken of another version"
    ]


def test_a_version_update_alone_passes_while_no_file_is_placed(tmp_path: Path) -> None:
    manifest = {"name": "x", "version": "2", "source": "s", "file": "meetings.min.js"}
    (tmp_path / "manifest.json").write_text(json.dumps({**manifest, "sha256": "", "sha256_of": ""}))

    assert problems(tmp_path) == []


def test_a_file_that_grew_past_the_gate_fails(tmp_path: Path) -> None:
    data = os.urandom(MAX_GZIP_BYTES + 100_000)  # random bytes do not compress

    assert problems(_placed(tmp_path, data)) == ["file is over 1.2 MB gzipped"]


def test_every_vendored_folder_has_a_manifest_that_passes() -> None:
    folders = [path for path in VENDOR.iterdir() if path.is_dir()]

    assert folders
    for folder in folders:
        assert problems(folder) == [], folder


def test_the_file_is_not_committed_until_the_licence_answer() -> None:
    tracked = subprocess.run(  # noqa: S603 - fixed arguments
        ["git", "ls-files", "--", FILE],  # noqa: S607 - git on the path
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    assert tracked == [], "ADR 0123 point 3: the SDK is not committed until Cisco has answered"


def test_the_file_is_ignored_by_git() -> None:
    ignored = subprocess.run(  # noqa: S603 - fixed arguments
        ["git", "check-ignore", "--", FILE],  # noqa: S607 - git on the path
        capture_output=True,
        text=True,
        check=False,
    )

    assert ignored.returncode == 0
