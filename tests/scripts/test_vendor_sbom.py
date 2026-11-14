import hashlib
import json
from pathlib import Path

import pytest

from app.adapters.vendored_files import MANIFEST, load
from scripts.vendor import VENDOR_DIRECTORY, manifests
from scripts.vendor_sbom import add_vendored, main

DATA = b"console.log(1);"
Sbom = dict[str, list[dict[str, object]]]


def _root(
    tmp_path: Path,
    *,
    placed: bytes | None,
    checksum_of: str | None = "3.13.0",
    sha256: str | None = None,
) -> Path:
    """A repository with the manifest, its file when ``placed``, and the checksum of ``DATA``."""
    folder = tmp_path / VENDOR_DIRECTORY / "webex"
    folder.mkdir(parents=True)
    fields: dict[str, object] = {
        "name": "webex",
        "version": "3.13.0",
        "source": "npm package webex",
        "file": "meetings.min.js",
    }
    if checksum_of is not None:
        fields["checksum"] = {
            "version": checksum_of,
            "sha256": sha256 or hashlib.sha256(DATA).hexdigest(),
        }
    (folder / MANIFEST).write_text(json.dumps(fields))
    if placed is not None:
        (folder / "meetings.min.js").write_bytes(placed)
    return tmp_path


def _sbom(tmp_path: Path) -> Path:
    path = tmp_path / "sbom.cdx.json"
    path.write_text(json.dumps({"bomFormat": "CycloneDX", "components": []}))
    return path


def test_a_placed_file_that_matches_its_checksum_is_listed(tmp_path: Path) -> None:
    sbom: Sbom = {"components": [{"bom-ref": "other"}]}

    outcome = add_vendored(sbom, _root(tmp_path, placed=DATA))

    entry = sbom["components"][1]
    assert (outcome.absent, outcome.wrong) == ([], [])
    assert entry["purl"] == "pkg:npm/webex@3.13.0"
    assert entry["version"] == "3.13.0"
    assert entry["hashes"] == [{"alg": "SHA-256", "content": hashlib.sha256(DATA).hexdigest()}]
    assert entry["externalReferences"][0]["url"].endswith("/webex/-/webex-3.13.0.tgz")  # type: ignore[index]


def test_an_absent_file_adds_nothing_and_the_command_succeeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, placed=None, checksum_of=None)
    path = _sbom(tmp_path)

    assert main(["vendor_sbom", str(path)], root) == 0

    assert json.loads(path.read_text())["components"] == []
    assert "vendored webex: file not placed; not listed" in capsys.readouterr().out


def test_the_command_lists_a_matching_file(tmp_path: Path) -> None:
    path = _sbom(tmp_path)

    assert main(["vendor_sbom", str(path)], _root(tmp_path, placed=DATA)) == 0

    assert [c["name"] for c in json.loads(path.read_text())["components"]] == ["webex"]


@pytest.mark.parametrize(
    "root_args",
    [
        {"placed": b"another file"},  # other bytes than the checksum names
        {"placed": DATA, "checksum_of": "3.12.0"},  # a checksum of an older version
        {"placed": DATA, "checksum_of": None},  # no checksum at all
    ],
    ids=["other-bytes", "other-version", "no-checksum"],
)
def test_a_placed_file_that_differs_fails_and_adds_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], root_args: dict[str, object]
) -> None:
    path = _sbom(tmp_path)
    before = path.read_text()

    assert main(["vendor_sbom", str(path)], _root(tmp_path, **root_args)) == 1  # type: ignore[arg-type]

    assert path.read_text() == before
    assert "not the one its manifest names" in capsys.readouterr().err


def test_running_twice_adds_it_once(tmp_path: Path) -> None:
    root = _root(tmp_path, placed=DATA)
    sbom: Sbom = {}
    add_vendored(sbom, root)
    add_vendored(sbom, root)

    assert len(sbom["components"]) == 1


def test_the_usage_is_checked(tmp_path: Path) -> None:
    assert main(["vendor_sbom"]) == 2


def test_the_repository_manifest_is_absent_and_adds_nothing() -> None:
    found = manifests()

    assert [m.name for m in found] == ["webex"]
    assert load(found[0].path) == found[0]
    if found[0].placed:
        pytest.skip("an installation placed the file (ADR 0123 point 3)")
    sbom: Sbom = {}
    outcome = add_vendored(sbom)
    assert sbom["components"] == []
    assert outcome.wrong == []
