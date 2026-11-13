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

from scripts.vendor import Manifest, file_matches, load, manifests

WEBEX = Path("app/web/static/vendor/webex")
MAX_GZIP_BYTES = 1_200 * 1024
"""The size gate of ADR 0123 point 3: growth past 1.2 MB gzipped is a decision, not a drift."""
FILE = "app/web/static/vendor/webex/meetings.min.js"


def problems(manifest: Manifest) -> list[str]:
    """Return what is wrong with a vendored folder; empty when it passes or is not placed."""
    if not manifest.placed:
        return []  # not placed (ADR 0123 point 3): the gates have nothing to check
    data = manifest.vendored.read_bytes()
    found = []
    if len(gzip.compress(data, mtime=0)) > MAX_GZIP_BYTES:
        found.append("file is over 1.2 MB gzipped")
    if not manifest.checksum_is_current:
        # Renovate moves ``version`` only: until the file and its checksum are replaced too, a
        # placed file is the old one (or has no checksum at all).
        found.append("no checksum of this version")
    elif not file_matches(manifest):
        found.append("sha256 differs from the manifest")
    return found


def _folder(
    folder: Path, data: bytes | None, *, version: str = "1", checksum: dict[str, str] | None = None
) -> Manifest:
    """Write a manifest (and ``data`` as its file, if given) and read it back."""
    fields: dict[str, object] = {"name": "x", "version": version, "source": "s", "file": "f.js"}
    if data is not None:
        (folder / "f.js").write_bytes(data)
        fields["checksum"] = checksum or {
            "version": "1",
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    (folder / "manifest.json").write_text(json.dumps(fields))
    return load(folder / "manifest.json")


def test_the_manifest_names_the_pinned_version_and_the_file() -> None:
    manifest = load(WEBEX / "manifest.json")

    assert manifest.name == "webex"
    assert manifest.version == "3.13.0"
    assert manifest.file == "meetings.min.js"
    assert "umd/meetings.min.js" in manifest.source
    assert manifest.sha256 is None or len(manifest.sha256) == 64


def test_the_manifest_has_no_checksum_while_the_file_is_not_placed() -> None:
    raw = json.loads((WEBEX / "manifest.json").read_text())

    assert set(raw) == {"name", "version", "source", "file"} | ({"checksum"} & set(raw))
    if not (WEBEX / "meetings.min.js").exists():
        assert "checksum" not in raw


def test_every_vendored_folder_passes_and_there_is_one() -> None:
    found = manifests(Path("."))

    assert found
    for manifest in found:
        assert problems(manifest) == [], manifest.path


def test_a_file_that_fits_and_matches_passes(tmp_path: Path) -> None:
    assert problems(_folder(tmp_path, b"console.log(1);" * 100)) == []


def test_a_file_that_differs_from_the_manifest_fails(tmp_path: Path) -> None:
    manifest = _folder(tmp_path, b"swapped", checksum={"version": "1", "sha256": "0"})

    assert problems(manifest) == ["sha256 differs from the manifest"]


def test_a_placed_file_without_a_checksum_fails(tmp_path: Path) -> None:
    (tmp_path / "f.js").write_bytes(b"x")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"name": "x", "version": "1", "source": "s", "file": "f.js"})
    )

    assert problems(load(tmp_path / "manifest.json")) == ["no checksum of this version"]


def test_a_version_update_alone_fails_where_the_file_is_placed(tmp_path: Path) -> None:
    # What a Renovate pull request does: it moves ``version`` and nothing else.
    assert problems(_folder(tmp_path, b"old file", version="2")) == ["no checksum of this version"]


def test_a_version_update_alone_passes_while_no_file_is_placed(tmp_path: Path) -> None:
    assert problems(_folder(tmp_path, None, version="2")) == []


def test_a_file_that_grew_past_the_gate_fails(tmp_path: Path) -> None:
    data = os.urandom(MAX_GZIP_BYTES + 100_000)  # random bytes do not compress

    assert problems(_folder(tmp_path, data)) == ["file is over 1.2 MB gzipped"]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed arguments
        ["git", *args],  # noqa: S607 - git on the path
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_file_is_not_committed_until_the_licence_answer() -> None:
    tracked = _git("ls-files", "--", FILE).stdout.split()

    assert tracked == [], "ADR 0123 point 3: the SDK is not committed until Cisco has answered"


def test_the_file_is_ignored_by_git() -> None:
    assert _git("check-ignore", "--", FILE).returncode == 0


def test_a_locally_built_image_cannot_carry_a_hand_placed_file() -> None:
    lines = Path(".dockerignore").read_text().splitlines()

    assert FILE in lines, "ADR 0123 point 3: the SDK is in no image before the licence answer"
