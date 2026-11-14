"""The test double of the call page's SDK stays in the end-to-end stack (ADR 0123 step 4c).

The browser tests of the joining flow run against a small stand-in (``e2e/vendor/webex/``) that
the end-to-end stack places over the vendored file with a manifest of its own. These tests keep
the pair consistent (the checksum is the stand-in's) and keep it out of every image and every
other place the application could serve it from: the guard reads every tracked file that could
place a file (workflows, compose files, Dockerfiles, charts, scripts, the Makefile), not a list
of the ones that exist today. Prose (``*.md``), the tests and ``e2e/``'s own sources are left
out because they describe the stand-in and place nothing.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from app.adapters.vendored_files import FileTrust, VendoredFile, load

STAND_IN = Path("e2e/vendor/webex")
ACTION = Path(".github/actions/e2e-stack/action.yml")
THIS = Path(__file__)
CONTAINER_FOLDER = "/srv/app/web/static/vendor/webex"
"""Where the application image keeps the vendored folder (``WORKDIR /srv``, ``COPY app ./app``)."""

VOLUME_OF_E2E = re.compile(r"""(?:-v|--volume)[ =]+["']?[^\s"':]*\be2e/?:([^\s"':]+)""")
"""A docker volume whose source is the whole of ``e2e/``; group 1 is where it goes."""
OTHER_PLACEMENT_OF_E2E = re.compile(
    r"""^\s*(?:COPY|ADD)\s[^#\n]*\be2e/?(?:\s|$)"""
    r"""|\bcp\s[^|\n]*\be2e/?(?:\s|$)"""
    r"""|\brsync\b[^\n]*\be2e/?(?:\s|$)"""
    r"""|^\s*-\s*["']?\.?/?e2e/?:"""
    r"""|\bsource:\s*["']?\.?/?e2e/?["']?\s*$""",
    re.MULTILINE,
)
"""A copy of ``e2e/`` as a whole by a Dockerfile, ``cp``, ``rsync`` or a compose volume."""
RUNNER_TARGET = "/e2e"
"""The Playwright runner container mounts ``e2e/`` here (ci.yml, tour.yml): not the application."""


def _tracked() -> list[Path]:
    """Return every tracked file that could place a file somewhere (see the module's docstring)."""
    names = subprocess.run(  # noqa: S603 - fixed arguments
        ["git", "ls-files"],  # noqa: S607 - git on the path
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    skipped = ("tests/", "e2e/", "docs/")
    return [
        Path(name)
        for name in names
        if name
        and not name.startswith(skipped)
        and not name.endswith(".md")
        and Path(name) not in {ACTION, THIS}
    ]


def _problems(text: str) -> list[str]:
    """Return what a file's text does that the stand-in's guard forbids."""
    found = []
    if "e2e/vendor" in text:
        found.append("names e2e/vendor")
    for target in VOLUME_OF_E2E.findall(text):
        if target != RUNNER_TARGET:
            found.append(f"mounts e2e/ as a whole at {target}")
    if OTHER_PLACEMENT_OF_E2E.search(text):
        found.append("copies e2e/ as a whole")
    return found


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
    mounts = re.findall(
        r'-v "\$PWD/e2e/vendor/webex/([\w.]+):(/srv/[^":]+):ro"', ACTION.read_text()
    )

    assert sorted(name for name, _ in mounts) == ["manifest.json", "meetings.min.js"]
    assert all(target == f"{CONTAINER_FOLDER}/{name}" for name, target in mounts)


def test_no_other_tracked_file_places_the_stand_in_or_the_whole_of_e2e_in_the_application() -> None:
    files = _tracked()

    assert any(path.name == "Dockerfile" for path in files), "the guard must see the Dockerfile"
    assert any(path.suffix in {".yml", ".yaml"} for path in files)
    found = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # a binary or a link: it places nothing by being read as text
        if problems := _problems(text):
            found[str(path)] = problems

    assert found == {}


@pytest.mark.parametrize(
    "text",
    [
        "COPY e2e ./e2e",
        "ADD --chown=1:1 e2e/ /srv/e2e/",
        "cp -r e2e /srv/app/web/static/vendor",
        "rsync -a e2e/ host:/srv",
        "    volumes:\n      - ./e2e:/srv/e2e",
        '        source: "./e2e"',
        'docker run -v "$PWD/e2e:/srv/app/web/static/vendor/webex" openplanner',
        "docker run --volume ./e2e/:/app/x img",
        "cp e2e/vendor/webex/meetings.min.js app/web/static/vendor/webex/",
    ],
)
def test_the_guard_sees_each_way_of_placing_it(text: str) -> None:
    assert _problems(text), text


@pytest.mark.parametrize(
    "text",
    [
        'docker run --rm -v "$PWD/e2e:/e2e" -w /e2e "$PLAYWRIGHT_IMAGE" npm ci',
        "working-directory: e2e",
        "cd e2e && npx playwright test",
        "COPY e2e-notes.txt .",
    ],
)
def test_the_guard_leaves_the_test_runner_and_ordinary_use_alone(text: str) -> None:
    assert _problems(text) == [], text


def test_the_manifest_of_the_stand_in_is_not_read_as_a_vendored_package() -> None:
    # The pins check, Renovate and the SBOM read `app/web/static/vendor/*/manifest.json` only.
    assert json.loads((STAND_IN / "manifest.json").read_text())["name"] == "webex"
    assert not str(STAND_IN).startswith("app/web/static/vendor")
