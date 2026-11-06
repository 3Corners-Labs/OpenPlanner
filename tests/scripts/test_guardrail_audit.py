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
    normalise_rulesets,
    render,
    unexpected_bypasses,
)
from tests.fakes.gh import FakeGh

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "guardrail-audit.yml"
TODAY = date(2026, 11, 2)
RELEASE_BOT = "3corners-openplanner-release[bot]"

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
        "bypasses": [{"actor_name": RELEASE_BOT, "ref": "refs/tags/v0.4.0-rc.9", "pushed_at": "t"}],
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
    findings = audit(baseline(tmp_path), live(tmp_path))

    assert not findings
    assert "Every guardrail matches" in render(findings, TODAY)


def test_a_ruleset_that_is_switched_off_is_drift(tmp_path: Path) -> None:
    expected = baseline(tmp_path)
    off = {**RULESET, "enforcement": "disabled"}

    findings = audit(expected, live(tmp_path, rulesets=[off]))

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

    drift = audit(expected, live(tmp_path, rulesets=[weaker])).drift

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

    drift = audit(expected, live(tmp_path, collaborators=more, installations=apps)).drift

    assert '`collaborators/new`: not in the baseline, found `"admin"`' in drift
    assert any(line.startswith("`installations/stranger`: not in the baseline") for line in drift)


def test_two_factor_switched_off_is_drift(tmp_path: Path) -> None:
    expected = baseline(tmp_path)

    drift = audit(
        expected, live(tmp_path, organisation={"two_factor_requirement_enabled": False})
    ).drift

    assert drift == [
        "`organisation/two_factor_requirement_enabled`: expected `true`, found `false`"
    ]


def test_a_section_that_cannot_be_read_is_reported(tmp_path: Path) -> None:
    expected = baseline(tmp_path)

    findings = audit(expected, live(tmp_path, collaborators=None, bypasses=None))

    assert findings.unreadable == ["collaborators", "bypasses"]
    assert "could not be read" in render(findings, TODAY)


def test_a_value_the_token_cannot_see_is_unreadable_not_drift(tmp_path: Path) -> None:
    """First audit run, 2026-11-02: the app saw ``allow_merge_commit`` as null."""
    expected = baseline(tmp_path)
    hidden_repo = {"visibility": "public", "allow_merge_commit": None}
    hidden_rules = [{**RULESET, "bypass_actors": None}]

    findings = audit(expected, live(tmp_path, repository=hidden_repo, rulesets=hidden_rules))

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
    assert [b["actor"] for b in expected["expected_bypasses"]] == [RELEASE_BOT]
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


def test_a_known_unseen_value_is_listed_but_closes_the_issue(tmp_path: Path) -> None:
    """Who may bypass the tag ruleset is never visible to a read-only token (2026-11-02)."""
    expected = baseline(tmp_path)
    expected["known_unseen"] = {"rulesets/main/bypass_actors": "never visible"}
    hidden_rules = [{**RULESET, "bypass_actors": None}]

    findings = audit(expected, live(tmp_path, rulesets=hidden_rules))

    assert not findings
    assert findings.known_unseen == ["rulesets/main/bypass_actors"]
    assert "Not checked, by design" in render(findings, TODAY)


def test_the_repositorys_baseline_names_why_each_value_is_known_unseen() -> None:
    known = json.loads(BASELINE.read_text())["known_unseen"]

    assert list(known) == ["rulesets/release tags/bypass_actors"]
    assert all(len(why) > 20 for why in known.values())
