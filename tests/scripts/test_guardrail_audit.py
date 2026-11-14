"""The guardrail audit reports drift, unreadable sections and unexpected bypasses (ADR 0109)."""

import json
import re
from datetime import date
from pathlib import Path

import pytest

from scripts.adr_issues import Issue
from scripts.guardrail_audit import (
    BASELINE,
    SECTIONS,
    audit,
    differences,
    main,
    normalise_policies,
    normalise_rulesets,
    render,
    unexpected_bypasses,
)
from tests.fakes.gh import FakeGh
from tests.scripts.conftest import workflow_events

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "guardrail-audit.yml"
GATE = ".github/workflows/decision-gate.yml"
TODAY = date(2026, 11, 2)
RELEASE_BOT = "3corners-openplanner-release[bot]"

POLICY = {
    "id": 7,
    "name": "decision gate",
    "target": "actions",
    "source_type": "Repository",
    "source": "o/r",
    "enforcement": "active",
    "conditions": {"workflow_path": {"include": [GATE], "exclude": []}},
    "rules": [
        {
            "type": "restrict_action_events",
            "parameters": {"allowed_events": ["pull_request_target", "merge_group"]},
        }
    ],
    "created_at": "2026-11-10T09:00:00Z",
}
"""The Actions event policy of ADR 0120 as the API returns it."""


def gate_run(number: int, event: str, conclusion: str, created_at: str) -> dict[str, object]:
    """One run of the decision gate as ``scripts/guardrail-fetch.sh`` keeps it."""
    return {
        "id": number,
        "event": event,
        "conclusion": conclusion,
        "created_at": created_at,
        "html_url": f"https://github.com/o/r/actions/runs/{number}",
    }


RULESET = {
    "id": 1,
    "name": "main",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
    "bypass_actors": [],
    "created_at": "2026-11-02T10:00:00Z",
    "rules": [
        {"type": "deletion"},
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [
                    {"context": "semgrep", "integration_id": 15368},
                    {"context": "decision gate", "integration_id": 15368},
                ]
            },
        },
    ],
}


def live(tmp_path: Path, **override: object) -> Path:
    """A directory of live files that matches ``baseline(tmp_path)``, with overrides."""
    files: dict[str, object] = {
        "rulesets": [RULESET],
        "repository": {"visibility": "public", "allow_merge_commit": False},
        "actions": {"enabled": True},
        "actions_workflow": {"default_workflow_permissions": "read"},
        "environments": [
            {
                "name": "release",
                "deployment_branch_policy": {"custom_branch_policies": True},
                "branches": [{"name": "main", "type": "branch"}],
            }
        ],
        "collaborators": [{"login": "owner", "role_name": "admin"}],
        "organisation": {"two_factor_requirement_enabled": True},
        "installations": [{"app_slug": "renovate", "repository_selection": "selected"}],
        "actions_policies": [POLICY],
        "bypasses": [{"actor_name": RELEASE_BOT, "ref": "refs/tags/v0.4.0-rc.9", "pushed_at": "t"}],
        "gate_runs": [gate_run(1, "merge_group", "success", "2026-11-02T09:00:00Z")],
    }
    files.update(override)
    directory = tmp_path / "live"
    directory.mkdir(exist_ok=True)
    for name, value in files.items():
        path = directory / f"{name}.json"
        if value is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(value))
    return directory


def baseline(tmp_path: Path) -> dict[str, object]:
    """The baseline that matches the default live files."""
    from scripts.guardrail_audit import snapshot

    found = snapshot(live(tmp_path))
    found["expected_bypasses"] = [{"actor": RELEASE_BOT, "ref_prefix": "refs/tags/v"}]
    return found


def test_a_matching_state_finds_nothing(tmp_path: Path) -> None:
    findings = audit(baseline(tmp_path), live(tmp_path), TODAY)

    assert not findings
    assert "Every guardrail matches" in render(findings, TODAY)


def test_a_ruleset_that_is_switched_off_is_drift(tmp_path: Path) -> None:
    expected = baseline(tmp_path)
    off = {**RULESET, "enforcement": "disabled"}

    findings = audit(expected, live(tmp_path, rulesets=[off]), TODAY)

    assert findings.drift == [
        '`rulesets/main/enforcement`: expected `"active"`, found `"disabled"`'
    ]


def test_a_new_bypass_actor_or_a_removed_check_is_drift(tmp_path: Path) -> None:
    expected = baseline(tmp_path)
    weaker = {
        **RULESET,
        "bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"}],
        "rules": [RULESET["rules"][0]],
    }

    drift = audit(expected, live(tmp_path, rulesets=[weaker]), TODAY).drift

    assert any(line.startswith("`rulesets/main/bypass_actors`") for line in drift)
    assert any("`rulesets/main/rules/required_status_checks`: expected" in line for line in drift)


def test_the_order_github_returns_checks_in_does_not_matter() -> None:
    shuffled = {**RULESET, "rules": [RULESET["rules"][1], RULESET["rules"][0]]}
    checks = shuffled["rules"][0]["parameters"]["required_status_checks"]
    shuffled["rules"][0] = {
        "type": "required_status_checks",
        "parameters": {"required_status_checks": list(reversed(checks))},
    }

    assert normalise_rulesets([shuffled]) == normalise_rulesets([RULESET])


def test_a_new_administrator_or_app_is_drift(tmp_path: Path) -> None:
    expected = baseline(tmp_path)
    more = [{"login": "owner", "role_name": "admin"}, {"login": "new", "role_name": "admin"}]
    apps = [
        {"app_slug": "renovate", "repository_selection": "selected"},
        {"app_slug": "stranger", "repository_selection": "all"},
    ]

    drift = audit(expected, live(tmp_path, collaborators=more, installations=apps), TODAY).drift

    assert '`collaborators/new`: not in the baseline, found `"admin"`' in drift
    assert any(line.startswith("`installations/stranger`: not in the baseline") for line in drift)


def test_two_factor_switched_off_is_drift(tmp_path: Path) -> None:
    expected = baseline(tmp_path)

    drift = audit(
        expected, live(tmp_path, organisation={"two_factor_requirement_enabled": False}), TODAY
    ).drift

    assert drift == [
        "`organisation/two_factor_requirement_enabled`: expected `true`, found `false`"
    ]


def test_a_section_that_cannot_be_read_is_reported(tmp_path: Path) -> None:
    expected = baseline(tmp_path)

    findings = audit(expected, live(tmp_path, collaborators=None, bypasses=None), TODAY)

    assert findings.unreadable == ["collaborators", "bypasses"]
    assert "could not be read" in render(findings, TODAY)


def test_a_value_the_token_cannot_see_is_unreadable_not_drift(tmp_path: Path) -> None:
    """First audit run, 2026-11-02: the app saw ``allow_merge_commit`` as null."""
    expected = baseline(tmp_path)
    hidden_repo = {"visibility": "public", "allow_merge_commit": None}
    hidden_rules = [{**RULESET, "bypass_actors": None}]

    findings = audit(expected, live(tmp_path, repository=hidden_repo, rulesets=hidden_rules), TODAY)

    assert findings.drift == []
    assert findings.unreadable == ["rulesets/main/bypass_actors", "repository/allow_merge_commit"]


def test_only_the_expected_bypass_is_quiet() -> None:
    expected = [{"actor": RELEASE_BOT, "ref_prefix": "refs/tags/v"}]
    bypasses = [
        {"actor_name": RELEASE_BOT, "ref": "refs/tags/v0.4.0"},
        {"actor_name": RELEASE_BOT, "ref": "refs/heads/main"},
        {"actor_name": "owner", "ref": "refs/tags/v0.4.0"},
    ]

    assert [b["ref"] for b in unexpected_bypasses(bypasses, expected)] == [
        "refs/heads/main",
        "refs/tags/v0.4.0",
    ]


def test_differences_name_missing_and_extra_keys() -> None:
    assert differences({"a": 1, "b": 2}, {"a": 1, "c": 3}, "x") == [
        "`x/b`: expected `2`, missing",
        "`x/c`: not in the baseline, found `3`",
    ]


def test_post_keeps_the_report_as_one_issue(tmp_path: Path) -> None:
    fake = FakeGh(issues=[Issue(4, "Guardrail drift 2026-11-01", "OPEN", frozenset())])
    path = tmp_path / "baseline.json"
    expected = baseline(tmp_path)
    expected["organisation"] = {"two_factor_requirement_enabled": False}
    path.write_text(json.dumps(expected))

    code = main(
        ["--live", str(live(tmp_path)), "--baseline", str(path), "--post", "--today", "2026-11-02"],
        run=fake,
    )

    assert code == 0
    assert fake.calls[-1][:5] == ["issue", "edit", "4", "--title", "Guardrail drift 2026-11-02"]


def test_an_unreadable_baseline_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--live", str(live(tmp_path)), "--baseline", str(tmp_path / "none.json")]) == 2
    assert "cannot be read" in capsys.readouterr().out


def test_the_snapshot_has_the_baselines_shape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--live", str(live(tmp_path)), "--snapshot"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == set(SECTIONS)


def test_the_repositorys_baseline_covers_every_section_and_expects_only_the_release_app() -> None:
    expected = json.loads(BASELINE.read_text())

    assert set(SECTIONS) <= set(expected)
    # ADR 0107: the release tags; ADR 0116: the release app creates a maintenance line.
    assert [b["actor"] for b in expected["expected_bypasses"]] == [RELEASE_BOT, RELEASE_BOT]
    assert [b["ref_prefix"] for b in expected["expected_bypasses"]] == [
        "refs/tags/v",
        "refs/heads/release/",
    ]
    assert expected["rulesets"]["main"]["bypass_actors"] == [], "ADR 0078: main has no bypass"
    assert expected["organisation"]["two_factor_requirement_enabled"] is True


def test_the_workflow_runs_hourly_in_the_audit_environment_and_posts() -> None:
    text = WORKFLOW.read_text()

    assert re.search(r'cron: "\d+ \* \* \* \*"', text), "hourly"
    assert "environment: audit" in text
    assert "actions/create-github-app-token@" in text
    assert "vars.AUDIT_APP_CLIENT_ID" in text
    assert "secrets.AUDIT_APP_PRIVATE_KEY" in text
    assert "scripts/guardrail-fetch.sh" in text
    assert "--post" in text
    assert "issues: write" in text
    # ADR 0120: the gate's runs are read with the workflow's token; the app has no Actions right.
    assert "actions: read" in text
    assert "RUNS_TOKEN: ${{ github.token }}" in text


def test_a_known_unseen_value_is_listed_but_closes_the_issue(tmp_path: Path) -> None:
    """Who may bypass the tag ruleset is never visible to a read-only token (2026-11-02)."""
    expected = baseline(tmp_path)
    expected["known_unseen"] = {"rulesets/main/bypass_actors": "never visible"}
    hidden_rules = [{**RULESET, "bypass_actors": None}]

    findings = audit(expected, live(tmp_path, rulesets=hidden_rules), TODAY)

    assert not findings
    assert findings.known_unseen == ["rulesets/main/bypass_actors"]
    assert "Not checked, by design" in render(findings, TODAY)


def test_the_repositorys_baseline_names_why_each_value_is_known_unseen() -> None:
    known = json.loads(BASELINE.read_text())["known_unseen"]

    assert list(known) == [
        "rulesets/release tags/bypass_actors",
        "rulesets/release line creation/bypass_actors",
        "actions_policies",
    ]
    assert all(len(why) > 20 for why in known.values())


# ADR 0116 point 3: the rulesets of a maintenance line release/X.Y.


def test_a_line_has_mains_rules_without_a_bypass_and_only_creation_is_bypassed() -> None:
    rulesets = json.loads(BASELINE.read_text())["rulesets"]
    main, line, creation = (
        rulesets["main"],
        rulesets["release lines"],
        rulesets["release line creation"],
    )
    expected = json.loads(json.dumps(main["rules"]))
    expected["required_status_checks"]["do_not_enforce_on_create"] = True
    # GitHub refuses a merge queue on a wildcard ref: a line's pull request must be up to date
    # with the line instead, and runs the full lane on the pull request (ci.yml).
    del expected["merge_queue"]
    expected["required_status_checks"]["strict_required_status_checks_policy"] = True

    assert line["rules"] == expected
    assert line["bypass_actors"] == main["bypass_actors"] == []
    assert line["conditions"] == creation["conditions"]
    assert creation["rules"] == {"creation": {}}


def test_every_full_lane_job_runs_on_a_pull_request_into_a_line() -> None:
    """A line has no merge queue (GitHub refuses one on a wildcard ref): its pull request runs
    the full lane itself, or a skipped required check would count as passed."""
    workflow = (BASELINE.parent / "workflows" / "ci.yml").read_text()
    full_lane = [
        block
        for block in re.split(r"\n  (?=[a-z0-9-]+:\n)", workflow)
        if "contains(github.event.pull_request.labels.*.name, 'ci:full')" in block
    ]

    # matrix, container, e2e-browser (one job per browser) and e2e (the required check's name).
    assert [block.split(":", 1)[0].strip() for block in full_lane] == [
        "matrix",
        "container",
        "e2e-browser",
        "e2e",
    ]
    assert all("startsWith(github.base_ref, 'release/')" in block for block in full_lane)


# ADR 0120: the event policy that keeps `pull_request_target` for the decision gate alone.


def test_a_removed_widened_or_switched_off_event_policy_is_drift(tmp_path: Path) -> None:
    expected = baseline(tmp_path)
    widened = {**POLICY, "conditions": {"workflow_path": {"include": ["~ALL"], "exclude": []}}}
    off = {**POLICY, "enforcement": "disabled"}
    another = {**POLICY, "name": "everything", "source_type": "Organization"}

    removed = audit(expected, live(tmp_path, actions_policies=[]), TODAY).drift
    wider = audit(expected, live(tmp_path, actions_policies=[widened]), TODAY).drift
    disabled = audit(expected, live(tmp_path, actions_policies=[off]), TODAY).drift
    added = audit(expected, live(tmp_path, actions_policies=[POLICY, another]), TODAY).drift

    assert [line.split(":")[0] for line in removed] == ["`actions_policies/decision gate`"]
    assert removed[0].endswith("missing")
    assert wider == [
        "`actions_policies/decision gate/conditions/workflow_path/include`: expected "
        f'`["{GATE}"]`, found `["~ALL"]`'
    ]
    assert disabled == [
        '`actions_policies/decision gate/enforcement`: expected `"active"`, found `"disabled"`'
    ]
    assert len(added) == 1
    assert added[0].startswith("`actions_policies/everything`: not in the baseline")


def test_the_order_and_form_github_returns_a_policy_in_do_not_matter() -> None:
    """Entered in the browser or through the API, in any order: the same policy."""
    other = {
        **POLICY,
        "id": 8,
        "conditions": {"workflow_path": {"include": [GATE]}},
        "rules": [
            {
                "type": "restrict_action_events",
                "parameters": {"allowed_events": ["merge_group", "pull_request_target"]},
            }
        ],
    }

    assert normalise_policies([other]) == normalise_policies([POLICY])


def test_policies_a_read_only_token_cannot_read_are_listed_not_a_finding(tmp_path: Path) -> None:
    """GitHub serves Actions policies only to Administration: write; the audit app reads."""
    expected = baseline(tmp_path)
    expected["known_unseen"] = {"actions_policies": "Administration: write only"}

    findings = audit(expected, live(tmp_path, actions_policies=None), TODAY)

    assert not findings
    assert findings.known_unseen == ["actions_policies"]
    assert "Not checked, by design" in render(findings, TODAY)
    del expected["known_unseen"]
    assert audit(expected, live(tmp_path, actions_policies=None), TODAY).unreadable == [
        "actions_policies"
    ]


def test_a_policies_file_that_cannot_be_parsed_is_unreadable_not_known(tmp_path: Path) -> None:
    """Only a refused read is unseen by design; ``null`` or a policy without a name is not."""
    expected = baseline(tmp_path)
    expected["known_unseen"] = {"actions_policies": "Administration: write only"}

    for broken in ("null", '[{"enforcement": "active"}]'):
        directory = live(tmp_path)
        (directory / "actions_policies.json").write_text(broken)
        findings = audit(expected, directory, TODAY)

        assert findings.unreadable == ["actions_policies"]
        assert findings.known_unseen == []


def test_an_administrators_full_run_must_read_every_section(tmp_path: Path) -> None:
    """``--full``: an administrator's login sees what the app cannot, so nothing is unseen."""
    expected = baseline(tmp_path)
    expected["known_unseen"] = {
        "actions_policies": "Administration: write only",
        "rulesets/main/bypass_actors": "never visible to the app",
    }
    directory = live(tmp_path, actions_policies=None, rulesets=[{**RULESET, "bypass_actors": None}])
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(expected))

    findings = audit(expected, directory, TODAY, full=True)

    assert findings.known_unseen == []
    assert findings.unreadable == ["rulesets/main/bypass_actors", "actions_policies"]
    assert main(["--live", str(directory), "--baseline", str(path), "--full"]) == 0


def test_a_gate_run_that_github_did_not_start_is_a_finding(tmp_path: Path) -> None:
    """A missing or narrowed policy shows as runs without a job, from the day before on."""
    runs = [
        gate_run(11, "pull_request_target", "startup_failure", "2026-11-02T16:05:00Z"),
        gate_run(12, "merge_group", "startup_failure", "2026-11-01T17:00:00Z"),
        gate_run(13, "pull_request_target", "startup_failure", "2026-10-31T16:30:00Z"),
        gate_run(14, "pull_request_target", "failure", "2026-11-02T10:00:00Z"),
    ]

    findings = audit(baseline(tmp_path), live(tmp_path, gate_runs=runs), TODAY)
    text = render(findings, TODAY)

    assert [run["id"] for run in findings.gate_not_started] == [11, 12]
    assert findings
    assert "## Runs of the decision gate that GitHub did not start" in text
    assert (
        "pull_request_target at 2026-11-02T16:05:00Z: https://github.com/o/r/actions/runs/11"
        in (text)
    )
    assert "runs/13" not in text
    assert "runs/14" not in text, "a red gate is the gate's own answer, not a blocked run"


def test_gate_runs_that_cannot_be_read_fail_closed(tmp_path: Path) -> None:
    no_time = {"id": 15, "event": "merge_group", "conclusion": "startup_failure"}
    bad_time = gate_run(16, "merge_group", "startup_failure", "yesterday")

    for runs in (None, [no_time], [bad_time]):
        findings = audit(baseline(tmp_path), live(tmp_path, gate_runs=runs), TODAY)

        assert findings.unreadable == ["gate_runs"]


def test_the_event_policy_allows_the_gate_alone_with_exactly_its_events() -> None:
    policies = json.loads(BASELINE.read_text())["actions_policies"]
    policy = policies["decision gate"]

    assert list(policies) == ["decision gate"]
    assert policy["source_type"] == "Repository", "the narrowest place (ADR 0120 point 1)"
    assert policy["enforcement"] == "active"
    assert policy["conditions"] == {"workflow_path": {"include": [GATE], "exclude": []}}
    assert list(policy["rules"]) == ["restrict_action_events"], (
        "no actor rule: whose label counts is the gate's own judgement (ADR 0104)"
    )
    # Without merge_group the policy would stop the gate in the merge queue (ADR 0088).
    assert policy["rules"]["restrict_action_events"]["allowed_events"] == sorted(
        workflow_events(ROOT / GATE)
    )


def test_only_the_decision_gate_uses_pull_request_target() -> None:
    """The policy allows the event for one file; no other workflow may need it (ADR 0120)."""
    users = sorted(
        workflow.name
        for workflow in [*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]
        if "pull_request_target" in workflow_events(workflow)
    )

    assert users == ["decision-gate.yml"]
