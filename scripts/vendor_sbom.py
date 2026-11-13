"""Add the vendored front-end files that ship to a CycloneDX SBOM (ADR 0118 point 6, ADR 0123).

``syft`` lists what it finds in the image: Python packages and system packages. A vendored
JavaScript file carries no package metadata, so a scan of the image never lists it. This script
adds one component per vendored manifest to the SBOM that syft wrote, but only when the file
is placed next to its manifest and its checksum is of the manifest's version: the SBOM must not
list a package that no image contains (it is attested, and ``grype`` would fail every pull
request for it). While the file is absent (ADR 0123 point 3: not committed until the licence
answer) nothing is added and the script says so. The component carries an npm package URL, so
``grype`` reports advisories of that version once it ships.

Usage: ``python3 -m scripts.vendor_sbom sbom.cdx.json`` from the repository root (CI and the
publish action; standard library only). Running it twice changes nothing.
"""

import json
import sys
from pathlib import Path

from scripts.vendor import ROOT, Manifest, manifests

REGISTRY = "https://registry.npmjs.org"


def shipped(manifest: Manifest) -> bool:
    """Whether the manifest's file is placed and its checksum is of the pinned version."""
    return manifest.placed and manifest.checksum_is_current


def component(manifest: Manifest, root: Path = ROOT) -> dict[str, object]:
    """Return the CycloneDX component of one vendored package that ships."""
    purl = f"pkg:npm/{manifest.name}@{manifest.version}"
    tarball = f"{manifest.name}-{manifest.version}.tgz"
    return {
        "type": "library",
        "bom-ref": purl,
        "name": manifest.name,
        "version": manifest.version,
        "purl": purl,
        "description": "Vendored as one file, not installed by a package manager (ADR 0118).",
        "hashes": [{"alg": "SHA-256", "content": manifest.sha256}],
        "externalReferences": [
            {"type": "distribution", "url": f"{REGISTRY}/{manifest.name}/-/{tarball}"}
        ],
        "properties": [
            {
                "name": "openplanner:vendored-file",
                "value": manifest.vendored.relative_to(root).as_posix(),
            }
        ],
    }


def add_vendored(sbom: dict[str, list[dict[str, object]]], root: Path = ROOT) -> list[str]:
    """Add every vendored component that ships and is not listed yet; return what was skipped.

    Returns:
        The names of the manifests whose file is not placed or whose checksum is not of the
        pinned version; none of them is added.
    """
    components = sbom.setdefault("components", [])
    listed = {entry.get("bom-ref") for entry in components}
    skipped: list[str] = []
    for manifest in manifests(root):
        if not shipped(manifest):
            skipped.append(manifest.name)
        elif (entry := component(manifest, root))["bom-ref"] not in listed:
            components.append(entry)
    return skipped


def main(argv: list[str]) -> int:
    """Add the vendored components that ship to the SBOM file named in ``argv``."""
    if len(argv) != 2:
        print("usage: python3 -m scripts.vendor_sbom SBOM.cdx.json", file=sys.stderr)
        return 2
    path = Path(argv[1])
    sbom = json.loads(path.read_text(encoding="utf-8"))
    skipped = add_vendored(sbom)
    path.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    for name in skipped:
        print(f"vendored {name}: file not placed (or checksum of another version); not listed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
