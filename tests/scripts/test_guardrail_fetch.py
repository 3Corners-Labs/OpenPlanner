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

from tests.support.fake_programs import fake_program

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

POLICY = {"id": 7, "name": "decision gate", "enforcement": "active"}
RUN = {
    "id": 3,
    "event": "merge_group",
    "conclusion": "startup_failure",
    "created_at": "2026-11-16T16:05:00Z",
    "html_url": "https://github.com/o/r/actions/runs/3",
    "head_sha": "abc",
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
  *"/actions/policies?has_parents=true"*) [ -n "$FAKE_POLICIES" ] || exit 1
    answer "$FAKE_POLICIES" "$q" ;;
  *"/actions/workflows/decision-gate.yml/runs"*) [ "$GH_TOKEN" = runs-token ] || exit 1
    answer "$FAKE_RUNS" "$q" ;;
  *"/actions/permissions"*) answer '{}' "$q" ;;
  "api repos/o/r") answer '{"visibility":"public","allow_merge_commit":null}' "$q" ;;
  *) exit 1 ;;
esac
"""


def fetch(tmp_path: Path, graph: object = GRAPH, **env: str) -> Path:
    """Run the fetch script against the fake ``gh``; return the output directory.

    ``env`` adds or replaces environment variables: ``RUNS_TOKEN`` (the workflow's token for
    the decision gate's runs) and ``FAKE_POLICIES`` (the policies answer, empty for a refusal).
    """
    fakes = tmp_path / "bin"
    fakes.mkdir(parents=True)
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
            "FAKE_POLICIES": json.dumps({"total_count": 1, "policies": [POLICY]}),
            "FAKE_RUNS": json.dumps({"workflow_runs": [RUN]}),
            "GH_TOKEN": "app-token",
            "RUNS_TOKEN": "runs-token",
            **env,
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


def test_bypass_actors_shown_without_their_mode_stay_unknown(tmp_path: Path) -> None:
    """The audit app's token got the actors from GraphQL with ``bypassMode`` null (2026-11-02)."""
    half = json.loads(json.dumps(GRAPH))
    for node in half["rulesets"]["nodes"][1]["bypassActors"]["nodes"]:
        node["bypassMode"] = None

    rulesets = {r["name"]: r for r in read(fetch(tmp_path, graph=half), "rulesets")}

    assert rulesets["release tags"]["bypass_actors"] is None
    assert rulesets["main"]["bypass_actors"] == []


# ADR 0120: the Actions event policies and the decision gate's runs.


def test_the_event_policies_are_read_with_those_of_the_levels_above(tmp_path: Path) -> None:
    assert read(fetch(tmp_path), "actions_policies") == [POLICY]


def test_policies_the_token_may_not_read_leave_the_section_out(tmp_path: Path) -> None:
    """GitHub serves them only to Administration: write; the audit app's read is refused."""
    out = fetch(tmp_path, FAKE_POLICIES="")

    assert not (out / "actions_policies.json").exists()
    assert (out / "rulesets.json").exists(), "the other sections are read as before"


def test_the_gate_runs_are_read_with_the_workflows_own_token(tmp_path: Path) -> None:
    """The audit app has no Actions permission; ``RUNS_TOKEN`` reads the runs."""
    runs = read(fetch(tmp_path), "gate_runs")

    assert runs == [
        {key: RUN[key] for key in ("id", "event", "conclusion", "created_at", "html_url")}
    ]
    assert not (fetch(tmp_path / "app", RUNS_TOKEN="") / "gate_runs.json").exists(), (
        "without RUNS_TOKEN the app's token is used, and it may not read runs"
    )
