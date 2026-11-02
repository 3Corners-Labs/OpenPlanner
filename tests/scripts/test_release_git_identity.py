"""Release tags and the release PR come from the release app, not a workflow token (ADR 0107).

``scripts/release-git-identity.sh`` makes the app the author; the workflow checks below keep
the three jobs that tag or open the release PR on the app's token inside the environment
``release``, and keep ``main.yml`` from starting again on the candidate tag the app pushes.
"""

import re
import subprocess
from pathlib import Path

import pytest

from tests.scripts.conftest import Git, fake_program, script_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release-git-identity.sh"
MAIN = (ROOT / ".github" / "workflows" / "main.yml").read_text()
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text()
APP_ACTION = "actions/create-github-app-token@"


def run(fakes: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the script in the test repository with the fakes first on its PATH."""
    return subprocess.run(  # noqa: S603 - the repository's own script and a fake gh
        [str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": script_path(fakes), "HOME": str(fakes), **env},
        check=False,
    )


def test_the_app_becomes_the_author(git: Git, tmp_path: Path) -> None:
    fake_program(tmp_path, "gh", 'printf "%s\\n" "$@" > "$FAKE_LOG"; echo 4242')
    log = tmp_path / "log"

    result = run(tmp_path, APP_SLUG="openplanner-release", FAKE_LOG=str(log))

    assert result.returncode == 0, result.stderr
    assert "/users/openplanner-release%5Bbot%5D" in log.read_text()
    assert git("config", "user.name").strip() == "openplanner-release[bot]"
    assert git("config", "user.email").strip() == (
        "4242+openplanner-release[bot]@users.noreply.github.com"
    )


def test_without_the_slug_nothing_is_configured(git: Git, tmp_path: Path) -> None:
    fake_program(tmp_path, "gh", "echo 4242")

    result = run(tmp_path)

    assert result.returncode != 0
    assert "slug of the release app" in result.stderr


def test_no_workflow_asks_for_a_personal_release_token() -> None:
    workflows = (ROOT / ".github" / "workflows").glob("*.yml")
    assert not [path.name for path in workflows if "RELEASE_TOKEN" in path.read_text()]


@pytest.mark.parametrize("job", ["release-pr", "tag"])
def test_the_release_jobs_take_the_apps_token_in_the_release_environment(job: str) -> None:
    start = RELEASE.index(f"\n  {job}:\n")
    following = re.search(r"\n  [a-z-]+:\n", RELEASE[start + 1 :])
    text = RELEASE[start : start + 1 + following.start()] if following else RELEASE[start:]

    assert "environment: ${{ !inputs.dry_run && 'release' || '' }}" in text
    assert APP_ACTION in text
    assert "secrets.RELEASE_APP_PRIVATE_KEY" in text
    assert "vars.RELEASE_APP_CLIENT_ID" in text
    assert "scripts/release-git-identity.sh" in text
    assert "github-actions[bot]" not in text


def test_the_candidate_is_tagged_by_the_app_and_does_not_start_main_yml_again() -> None:
    candidate = MAIN[MAIN.index("name: release candidate from main") : MAIN.index("\n  publish:")]

    assert '"!v*.*.*-rc.*"' in MAIN, "an app's tag push starts workflows"
    assert "environment: release" in candidate
    assert APP_ACTION in candidate
    assert "scripts/release-git-identity.sh" in candidate
    assert 'http.https://github.com/.extraheader=" ' in candidate, "the checkout header wins"
    assert "x-access-token:${APP_TOKEN}@github.com" in candidate
