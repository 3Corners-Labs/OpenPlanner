"""What a vendored manifest may name, and when it is read (ADR 0123 point 3)."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.adapters import vendored_files
from app.adapters.vendored_files import FileTrust, VendoredFile, load


def _manifest(folder: Path, name: str, data: bytes | None = None) -> Path:
    """Write a manifest that names ``name`` and, with ``data``, a checksum of it."""
    fields: dict[str, object] = {"name": "x", "version": "1", "source": "s", "file": name}
    if data is not None:
        fields["checksum"] = {"version": "1", "sha256": hashlib.sha256(data).hexdigest()}
    path = folder / "manifest.json"
    path.write_text(json.dumps(fields))
    return path


def test_a_manifest_that_names_a_path_is_refused_and_means_no_file(tmp_path: Path) -> None:
    folder = tmp_path / "vendor"
    folder.mkdir()
    (tmp_path / "outside.js").write_bytes(b"x")
    path = _manifest(folder, "../outside.js", b"x")

    with pytest.raises(ValueError, match="bare file name"):
        load(path)
    assert VendoredFile(folder).trust() is FileTrust.NOT_PLACED
    assert VendoredFile(folder).offer() is None


@pytest.mark.parametrize("name", ["", ".", "..", "a/b.js", "/etc/passwd", "sub/../x.js"])
def test_only_a_bare_file_name_is_a_file_name(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="bare file name"):
        load(_manifest(tmp_path, name))


def test_a_bare_file_name_is_read(tmp_path: Path) -> None:
    assert load(_manifest(tmp_path, "meetings.min.js")).file == "meetings.min.js"


def test_the_manifest_is_read_again_only_when_it_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "f.js").write_bytes(b"abc")
    path = _manifest(tmp_path, "f.js", b"abc")
    file = VendoredFile(tmp_path)
    reads: list[Path] = []
    original = vendored_files.load

    def counting(manifest: Path) -> vendored_files.Manifest:
        reads.append(manifest)
        return original(manifest)

    monkeypatch.setattr(vendored_files, "load", counting)

    for _ in range(3):
        assert file.trust() is FileTrust.TRUSTED
        assert file.offer() is not None
    assert len(reads) == 1

    path.write_text(path.read_text() + " ")
    os.utime(path, ns=(2, 2))
    assert file.trust() is FileTrust.TRUSTED
    assert len(reads) == 2


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        '"text"',
        "null",
        '{"name": "x", "version": "1", "source": "s", "file": "f.js", "checksum": "abc"}',
    ],
)
def test_a_manifest_of_the_wrong_shape_is_no_offer_and_no_error(
    tmp_path: Path, content: str
) -> None:
    (tmp_path / "manifest.json").write_text(content)
    (tmp_path / "f.js").write_bytes(b"x")
    file = VendoredFile(tmp_path)

    assert file.trust() is FileTrust.NOT_PLACED
    assert file.offer() is None
