"""Add the vendored front-end files to a CycloneDX SBOM (ADR 0118 point 6, ADR 0123 point 3).

``syft`` lists what it finds in the image: Python packages and system packages. A vendored
JavaScript file carries no package metadata, so a scan of the image never lists it, and it is
not in the image at all until the licence answer of ADR 0123 point 18 allows it. This script
adds one component per ``app/web/static/vendor/*/manifest.json`` to the SBOM that syft wrote,
with the package URL, the version, the npm source and the property ``openplanner:placed`` that
says whether the file was in the checkout. The checksum is added only when the manifest has one.
Because the component carries an npm package URL, ``grype`` also reports advisories of that
version, which is the advisory check of ADR 0123 point 3.

Usage: ``python3 scripts/vendor_sbom.py sbom.cdx.json`` (CI and the publish action; only the
standard library, so no environment is needed). Running it twice changes nothing.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFESTS = "app/web/static/vendor/*/manifest.json"
REGISTRY = "https://registry.npmjs.org"


def component(manifest: dict[str, str], folder: Path, root: Path = ROOT) -> dict[str, object]:
    """Return the CycloneDX component of one vendored package.

    Args:
        manifest: The parsed ``manifest.json`` (name, version, file, sha256).
        folder: The folder of the manifest, to see whether the file is placed.
        root: The repository root, for the path recorded in the property.
    """
    name, version = manifest["name"], manifest["version"]
    purl = f"pkg:npm/{name}@{version}"
    vendored = folder / manifest["file"]
    placed = vendored.is_file()
    found: dict[str, object] = {
        "type": "library",
        "bom-ref": purl,
        "name": name,
        "version": version,
        "purl": purl,
        "description": "Vendored as one file, not installed by a package manager (ADR 0118).",
        "externalReferences": [
            {"type": "distribution", "url": f"{REGISTRY}/{name}/-/{name}-{version}.tgz"}
        ],
        "properties": [
            {"name": "openplanner:vendored-file", "value": vendored.relative_to(root).as_posix()},
            {"name": "openplanner:placed", "value": str(placed).lower()},
        ],
    }
    if manifest.get("sha256") and manifest.get("sha256_of") == version:
        found["hashes"] = [{"alg": "SHA-256", "content": manifest["sha256"]}]
    return found


def add_vendored(sbom: dict[str, object], root: Path = ROOT) -> dict[str, object]:
    """Add every vendored component that ``sbom`` does not list yet; return ``sbom``."""
    components: list[dict[str, object]] = sbom.setdefault("components", [])  # type: ignore[assignment]
    listed = {entry.get("bom-ref") for entry in components}
    for path in sorted(root.glob(MANIFESTS)):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        entry = component(manifest, path.parent, root)
        if entry["bom-ref"] not in listed:
            components.append(entry)
    return sbom


def main(argv: list[str]) -> int:
    """Add the vendored components to the SBOM file named in ``argv``."""
    if len(argv) != 2:
        print("usage: vendor_sbom.py SBOM.cdx.json", file=sys.stderr)
        return 2
    path = Path(argv[1])
    sbom = add_vendored(json.loads(path.read_text(encoding="utf-8")))
    path.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
