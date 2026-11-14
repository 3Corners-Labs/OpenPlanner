"""The test double of the call page's SDK stays in the end-to-end stack (ADR 0123 step 4c).

The browser tests of the joining flow run against a small stand-in (``e2e/vendor/webex/``) that
the end-to-end stack places over the vendored file with a manifest of its own. These tests keep
the pair consistent (the checksum is the stand-in's) and keep it out of every image and every
other place the application could serve it from.
"""

import json
import re
from pathlib import Path

from app.adapters.vendored_files import FileTrust, VendoredFile, load

STAND_IN = Path("e2e/vendor/webex")
ACTION = Path(".github/actions/e2e-stack/action.yml")
CONTAINER_FOLDER = "/srv/app/web/static/vendor/webex"
"""Where the application image keeps the vendored folder (``WORKDIR /srv``, ``COPY app ./app``)."""


def test_the_stand_ins_manifest_names_the_stand_ins_bytes() -> None:
    # A change to the stand-in needs its new `shasum -a 256` in e2e/vendor/webex/manifest.json.
    assert VendoredFile(STAND_IN).trust() is FileTrust.TRUSTED


def test_the_stand_in_says_what_it_is_and_is_not_the_sdk() -> None:
    manifest = load(STAND_IN / "manifest.json")
    source = (STAND_IN / "meetings.min.js").read_text()

    assert "test-double" in manifest.version
    assert source.lstrip().startswith("/*\n * TEST DOUBLE")
    assert len(source) < 20_000, "a stand-in is small; the SDK is megabytes"


def test_the_stand_in_is_in_no_image() -> None:
    ignore = Path(".dockerignore").read_text().splitlines()
    whitelisted = [line for line in ignore if line.startswith("!")]

    assert "*" in ignore
    assert not any(line.removeprefix("!").startswith(("e2e", "e2e/")) for line in whitelisted)
    # The real vendored folder holds no copy of it, and the file of that folder stays ignored.
    assert not Path("app/web/static/vendor/webex/meetings.min.js").is_symlink()


def test_only_the_end_to_end_action_places_it_and_read_only() -> None:
    text = ACTION.read_text()
    mounts = re.findall(r'-v "\$PWD/e2e/vendor/webex/([\w.]+):(/srv/[^":]+):ro"', text)

    assert sorted(name for name, _ in mounts) == ["manifest.json", "meetings.min.js"]
    assert all(target == f"{CONTAINER_FOLDER}/{name}" for name, target in mounts)
    # Nothing else in the repository refers to the stand-in's folder as a source to place.
    others = [
        str(path)
        for path in [
            *Path(".github").rglob("*.yml"),
            Path("Dockerfile"),
            Path("docker-compose.yml"),
        ]
        if path != ACTION and "e2e/vendor" in path.read_text()
    ]
    assert others == []


def test_the_manifest_of_the_stand_in_is_not_read_as_a_vendored_package() -> None:
    # The pins check, Renovate and the SBOM read `app/web/static/vendor/*/manifest.json` only.
    assert json.loads((STAND_IN / "manifest.json").read_text())["name"] == "webex"
    assert not str(STAND_IN).startswith("app/web/static/vendor")
