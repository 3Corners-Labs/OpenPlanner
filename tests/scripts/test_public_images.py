"""Public images of a minor or major release (ADR 0108): the rule, the check and the notes."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).parents[2]
BASH = shutil.which("bash") or "/bin/bash"
APP = "ghcr.io/o/r-public@sha256:" + "a" * 64
PROXY = "ghcr.io/o/r-proxy-public@sha256:" + "b" * 64


def _check(tmp_path: Path, *refs: str, private: str = "") -> subprocess.CompletedProcess[str]:
    """Run check-public-image.sh with a fake docker that refuses the reference `private`."""
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\n[ -n "$DOCKER_CONFIG" ] && [ -z "$(ls -A "$DOCKER_CONFIG")" ] || exit 9\n'
        '[ "$3" != "$PRIVATE" ]\n'
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "PRIVATE": private}
    return subprocess.run(  # noqa: S603
        [BASH, str(REPO / "scripts/check-public-image.sh"), *refs],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_anonymous_pull_of_both_references_passes(tmp_path: Path) -> None:
    result = _check(tmp_path, APP, PROXY)
    assert result.returncode == 0, result.stderr
    assert f"public: {PROXY}" in result.stdout


def test_a_package_that_is_still_private_fails_the_release(tmp_path: Path) -> None:
    result = _check(tmp_path, APP, PROXY, private=PROXY)
    assert result.returncode == 1
    assert "set the package to Public" in result.stdout


def test_the_notes_carry_the_references_and_the_identity_of_the_tag() -> None:
    notes = subprocess.run(  # noqa: S603
        [BASH, str(REPO / "scripts/public-release-notes.sh"), "o/r", "v0.4.0", APP, PROXY],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert APP in notes
    assert PROXY in notes
    assert "https://github.com/o/r/.github/workflows/main.yml@refs/tags/v0.4.0" in notes
    assert "cosign verify-attestation" in notes
    assert "token.actions.githubusercontent.com" in notes


def test_only_a_minor_or_major_tag_selects_the_public_packages() -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/main.yml").read_text())
    steps = workflow["jobs"]["publish"]["steps"]
    rule = next(s for s in steps if s.get("id") == "public")["run"]
    assert "^v[0-9]+\\.[0-9]+\\.0$" in rule
    # the release-candidate job never gets a public image
    candidate = workflow["jobs"]["candidate"]["steps"] if "candidate" in workflow["jobs"] else []
    assert all("public-image" not in s.get("with", {}) for s in candidate)
    publishing = [s for s in steps if s.get("uses") == "./.github/actions/publish-image"]
    assert len(publishing) == 2
    assert all(
        s["with"]["public-image"].startswith("${{ steps.public.outputs.") for s in publishing
    )


def test_the_action_signs_and_attests_every_reference_it_pushed() -> None:
    action = yaml.safe_load((REPO / ".github/actions/publish-image/action.yml").read_text())
    assert action["inputs"]["public-image"]["default"] == ""
    steps = {s.get("name", s.get("id")): s for s in action["runs"]["steps"]}
    for name in ("sign image (keyless)", "attest SBOM"):
        assert "public-image" in steps[name]["env"]["IMAGE_REFS"]
