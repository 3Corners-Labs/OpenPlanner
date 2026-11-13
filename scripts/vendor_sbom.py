"""Add the vendored front-end files that ship to a CycloneDX SBOM (ADR 0118 point 6, ADR 0123).

``syft`` lists what it finds in the image: Python packages and system packages. A vendored
JavaScript file carries no package metadata, so a scan of the image never lists it. This script
adds one component per vendored manifest to the SBOM that syft wrote, with three outcomes:

- the file is absent: nothing is added and the script says so (exit 0). This is the state today;
- the file is placed and its bytes are the ones the manifest's checksum of the pinned version
  names (``scripts.vendor.file_matches``): the component is added;
- the file is placed but anything differs (no checksum, a checksum of another version, other
  bytes): the SBOM is left as it was and the script FAILS (exit 1) with the reason. A wrong file
  in a build context is not passed over.

The SBOM must not list a package that no image contains (it is attested, and ``grype`` would
fail every pull request for it). Note that ``.dockerignore`` keeps the file out of every image
today (ADR 0123 point 3), so the component can only appear once row 2b of the ADR removes that
line: the two go together. The component carries an npm package URL, so ``grype`` reports
advisories of that version once it ships.

Usage: ``python3 -m scripts.vendor_sbom sbom.cdx.json`` from the repository root (CI and the
publish action; standard library only). Running it twice changes nothing.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from scripts.vendor import ROOT, Manifest, file_matches, manifests

REGISTRY = "https://registry.npmjs.org"


@dataclass
class Outcome:
    """What ``add_vendored`` did with each manifest, by package name.

    Attributes:
        absent: The file is not placed; nothing was added.
        wrong: The file is placed but is not the one the manifest names; nothing was added.
    """

    absent: list[str] = field(default_factory=list)
    wrong: list[str] = field(default_factory=list)


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


def add_vendored(sbom: dict[str, list[dict[str, object]]], root: Path = ROOT) -> Outcome:
    """Add every vendored component whose file matches its manifest and is not listed yet."""
    components = sbom.setdefault("components", [])
    listed = {entry.get("bom-ref") for entry in components}
    outcome = Outcome()
    for manifest in manifests(root):
        if not manifest.placed:
            outcome.absent.append(manifest.name)
        elif not file_matches(manifest):
            outcome.wrong.append(manifest.name)
        elif (entry := component(manifest, root))["bom-ref"] not in listed:
            components.append(entry)
    return outcome


def main(argv: list[str], root: Path = ROOT) -> int:
    """Add the vendored components that ship to the SBOM in ``argv``; tests give ``root``."""
    if len(argv) != 2:
        print("usage: python3 -m scripts.vendor_sbom SBOM.cdx.json", file=sys.stderr)
        return 2
    path = Path(argv[1])
    sbom = json.loads(path.read_text(encoding="utf-8"))
    outcome = add_vendored(sbom, root)
    for name in outcome.absent:
        print(f"vendored {name}: file not placed; not listed")
    if outcome.wrong:
        for name in outcome.wrong:
            print(
                f"vendored {name}: the placed file is not the one its manifest names "
                "(no checksum, a checksum of another version, or other bytes); SBOM not changed",
                file=sys.stderr,
            )
        return 1
    path.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
