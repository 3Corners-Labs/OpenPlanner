"""The fetch script reads what a read-only app is denied a second way (ADR 0109 step 3).

The first audit run on 2026-11-02 showed that the audit app's token gets null merge settings,
empty bypass actors and an empty collaborator list from REST. The fake ``gh`` below answers
the same way, and GraphQL and the per-user role fill the gaps.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.scripts.conftest import fake_program

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "guardrail-fetch.sh"

RULESETS = [{"id": 1, "name": "main"}, {"id": 2, "name": "release tags"}]
GRAPH = {
    "mergeCommitAllowed": False,
    "rebaseMergeAllowed": False,
    "squashMergeAllowed": True,
    "deleteBranchOnMerge": True,
    "autoMergeAllowed": True,
    "rulesets": {
        "nodes": [
            {"name": "main", "bypassActors": {"nodes": []}},
            {
                "name": "release tags",
                "bypassActors": {
                    "nodes": [
                        {
                            "bypassMode": "ALWAYS",
                            "repositoryRoleDatabaseId": None,
                            "actor": {"__typename": "App", "databaseId": 77},
                        },
                        {"bypassMode": "ALWAYS", "repositoryRoleDatabaseId": 5, "actor": None},
                    ]
                },
            },
        ]
    },
}

FAKE_GH = r"""
args="$*"
answer() { printf '%s' "$1" | if [ "${2:-}" ]; then jq -r "$2"; else cat; fi; }
q=""; prev=""
for a in "$@"; do [ "$prev" = "--jq" ] && q="$a"; prev="$a"; done
case "$args" in
  "api graphql"*)
    [ -n "$FAKE_GRAPH" ] || exit 1
    answer "{\"data\":{\"repository\":$FAKE_GRAPH}}" "$q" ;;
  *"rulesets/rule-suites"*) answer '[]' "$q" ;;
  *"/rulesets/1"*) answer '{"name":"main","bypass_actors":[],"rules":[]}' "$q" ;;
  *"/rulesets/2"*) answer '{"name":"release tags","bypass_actors":[],"rules":[]}' "$q" ;;
  *"/rulesets"*) answer "$FAKE_RULESETS" "$q" ;;
  *"collaborators/owner/permission"*) answer '{"permission":"admin","role_name":"admin"}' "$q" ;;
  *"collaborators/writer/permission"*) answer '{"permission":"write","role_name":"write"}' "$q" ;;
  *"collaborators?"*) answer '[]' "$q" ;;
  *"members?role=admin"*) answer '[{"login":"owner"}]' "$q" ;;
  *"/environments"*) answer '{"environments":[]}' "$q" ;;
  *"/installations"*) answer '{"installations":[]}' "$q" ;;
  "api orgs/"*) answer '{"two_factor_requirement_enabled":true}' "$q" ;;
  *"/actions/permissions"*) answer '{}' "$q" ;;
  "api repos/o/r") answer '{"visibility":"public","allow_merge_commit":null}' "$q" ;;
  *) exit 1 ;;
esac
"""


def fetch(tmp_path: Path, graph: object = GRAPH) -> Path:
    """Run the fetch script against the fake ``gh``; return the output directory."""
    fakes = tmp_path / "bin"
    fakes.mkdir()
    fake_program(fakes, "gh", FAKE_GH)
    baseline = tmp_path / "guardrails.json"
    baseline.write_text(json.dumps({"collaborators": {"owner": "admin", "writer": "write"}}))
    out = tmp_path / "live"
    subprocess.run(  # noqa: S603 - the repository's own script and a fake gh
        [str(SCRIPT), str(out), "o/r"],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "PATH": f"{fakes}{os.pathsep}{os.environ['PATH']}",
            "GUARDRAILS_BASELINE": str(baseline),
            "FAKE_RULESETS": json.dumps(RULESETS),
            "FAKE_GRAPH": json.dumps(graph) if graph else "",
        },
    )
    return out


def read(out: Path, name: str) -> object:
    return json.loads((out / f"{name}.json").read_text())


@pytest.fixture(autouse=True)
def _needs_jq() -> None:
    if subprocess.run(["which", "jq"], capture_output=True, check=False).returncode:  # noqa: S607
        pytest.skip("jq is not installed")


def test_merge_settings_come_from_graphql_when_rest_hides_them(tmp_path: Path) -> None:
    repository = read(fetch(tmp_path), "repository")

    assert repository["allow_merge_commit"] is False
    assert repository["allow_squash_merge"] is True
    assert repository["delete_branch_on_merge"] is True


def test_bypass_actors_come_from_graphql_when_rest_hides_them(tmp_path: Path) -> None:
    rulesets = {r["name"]: r for r in read(fetch(tmp_path), "rulesets")}

    assert rulesets["main"]["bypass_actors"] == []
    assert rulesets["release tags"]["bypass_actors"] == [
        {"actor_type": "Integration", "actor_id": 77, "bypass_mode": "always"},
        {"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"},
    ]


def test_bypass_actors_that_nothing_shows_stay_unknown(tmp_path: Path) -> None:
    rulesets = read(fetch(tmp_path, graph=None), "rulesets")

    assert all(r["bypass_actors"] is None for r in rulesets)


def test_collaborators_come_from_each_users_role_and_the_owners(tmp_path: Path) -> None:
    collaborators = read(fetch(tmp_path), "collaborators")

    assert collaborators == [
        {"login": "owner", "role_name": "admin"},
        {"login": "writer", "role_name": "write"},
    ]
