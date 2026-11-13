import json
from pathlib import Path

from scripts.vendor_sbom import ROOT, add_vendored, main


def _root(tmp_path: Path, *, placed: bool, sha: str = "", sha_of: str = "") -> Path:
    folder = tmp_path / "app/web/static/vendor/webex"
    folder.mkdir(parents=True)
    manifest = {
        "name": "webex",
        "version": "3.13.0",
        "file": "meetings.min.js",
        "sha256": sha,
        "sha256_of": sha_of,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))
    if placed:
        (folder / "meetings.min.js").write_text("x")
    return tmp_path


def test_the_manifest_is_listed_as_a_component_with_version_and_source(tmp_path: Path) -> None:
    sbom = add_vendored({"components": [{"bom-ref": "other"}]}, _root(tmp_path, placed=False))

    entry = sbom["components"][1]  # type: ignore[index]
    assert entry["purl"] == "pkg:npm/webex@3.13.0"
    assert entry["version"] == "3.13.0"
    assert entry["externalReferences"][0]["url"].endswith("/webex/-/webex-3.13.0.tgz")
    assert {"name": "openplanner:placed", "value": "false"} in entry["properties"]
    assert "hashes" not in entry


def test_a_placed_file_with_a_checksum_of_this_version_carries_it(tmp_path: Path) -> None:
    sbom = add_vendored({}, _root(tmp_path, placed=True, sha="ab" * 32, sha_of="3.13.0"))

    entry = sbom["components"][0]  # type: ignore[index]
    assert entry["hashes"] == [{"alg": "SHA-256", "content": "ab" * 32}]
    assert {"name": "openplanner:placed", "value": "true"} in entry["properties"]


def test_a_checksum_of_another_version_is_not_listed(tmp_path: Path) -> None:
    sbom = add_vendored({}, _root(tmp_path, placed=True, sha="ab" * 32, sha_of="3.12.0"))

    assert "hashes" not in sbom["components"][0]  # type: ignore[index]


def test_running_twice_adds_it_once(tmp_path: Path) -> None:
    root = _root(tmp_path, placed=False)
    sbom = add_vendored(add_vendored({}, root), root)

    assert len(sbom["components"]) == 1  # type: ignore[arg-type]


def test_the_command_rewrites_the_file_of_the_repository_sbom_shape(tmp_path: Path) -> None:
    path = tmp_path / "sbom.cdx.json"
    path.write_text(json.dumps({"bomFormat": "CycloneDX", "components": []}))

    assert main(["vendor_sbom.py", str(path)]) == 0
    listed = json.loads(path.read_text())["components"]

    assert [entry["name"] for entry in listed] == ["webex"]
    assert (ROOT / "app/web/static/vendor/webex/manifest.json").is_file()
    assert main(["vendor_sbom.py"]) == 2
