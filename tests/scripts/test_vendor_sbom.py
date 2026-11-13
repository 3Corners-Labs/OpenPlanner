import hashlib
import json
from pathlib import Path

import pytest

from scripts.vendor import MANIFEST, VENDOR_DIRECTORY, load, manifests
from scripts.vendor_sbom import add_vendored, main

DATA = b"console.log(1);"


def _root(tmp_path: Path, *, placed: bool, checksum_of: str | None = "3.13.0") -> Path:
    folder = tmp_path / VENDOR_DIRECTORY / "webex"
    folder.mkdir(parents=True)
    fields: dict[str, object] = {
        "name": "webex",
        "version": "3.13.0",
        "source": "npm package webex",
        "file": "meetings.min.js",
    }
    if checksum_of is not None:
        fields["checksum"] = {"version": checksum_of, "sha256": hashlib.sha256(DATA).hexdigest()}
    (folder / MANIFEST).write_text(json.dumps(fields))
    if placed:
        (folder / "meetings.min.js").write_bytes(DATA)
    return tmp_path


def test_a_placed_file_with_a_checksum_of_this_version_is_listed(tmp_path: Path) -> None:
    sbom: dict[str, list[dict[str, object]]] = {"components": [{"bom-ref": "other"}]}

    skipped = add_vendored(sbom, _root(tmp_path, placed=True))

    entry = sbom["components"][1]
    assert skipped == []
    assert entry["purl"] == "pkg:npm/webex@3.13.0"
    assert entry["version"] == "3.13.0"
    assert entry["hashes"] == [{"alg": "SHA-256", "content": hashlib.sha256(DATA).hexdigest()}]
    assert entry["externalReferences"][0]["url"].endswith("/webex/-/webex-3.13.0.tgz")  # type: ignore[index]


def test_an_absent_file_adds_nothing(tmp_path: Path) -> None:
    sbom: dict[str, list[dict[str, object]]] = {"components": []}

    assert add_vendored(sbom, _root(tmp_path, placed=False, checksum_of=None)) == ["webex"]
    assert sbom["components"] == []


def test_a_checksum_of_another_version_adds_nothing(tmp_path: Path) -> None:
    sbom: dict[str, list[dict[str, object]]] = {}

    assert add_vendored(sbom, _root(tmp_path, placed=True, checksum_of="3.12.0")) == ["webex"]
    assert sbom["components"] == []


def test_running_twice_adds_it_once(tmp_path: Path) -> None:
    root = _root(tmp_path, placed=True)
    sbom: dict[str, list[dict[str, object]]] = {}
    add_vendored(sbom, root)
    add_vendored(sbom, root)

    assert len(sbom["components"]) == 1


def test_the_command_says_so_when_nothing_is_listed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "sbom.cdx.json"
    path.write_text(json.dumps({"bomFormat": "CycloneDX", "components": []}))

    assert main(["vendor_sbom", str(path)]) == 0

    # The repository's own manifest: its file is not placed, so the SBOM stays as syft wrote it.
    assert json.loads(path.read_text())["components"] == []
    assert "vendored webex: file not placed" in capsys.readouterr().out
    assert main(["vendor_sbom"]) == 2


def test_the_reader_reads_the_repository_manifest() -> None:
    found = manifests()

    assert [m.name for m in found] == ["webex"]
    assert load(found[0].path) == found[0]
