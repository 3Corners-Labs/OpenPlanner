"""Compare the live guardrails with the baseline in the repository and report the drift (ADR 0109).

The guardrails of 2026-11-02 live in GitHub's settings, not in code: rulesets, merge and
security settings, the workflow token's defaults, environments, who has more than read access,
the organisation's two-factor requirement and the installed apps. ``.github/guardrails.json``
says how they should be; ``scripts/guardrail-fetch.sh`` reads how they are, one JSON file per
section, and this script compares the two without any network call:

- every value that differs from the baseline, by its path (``rulesets/main/enforcement``);
- every section that could not be read, and every value the token sees as null where the
  baseline expects one (the audit fails closed, and says what it could not check);
- every bypass of a ruleset in the last day that the baseline does not expect (the release
  app creating a ``v*`` tag is expected).

``--post`` keeps the report as the one open issue ``Guardrail drift <date>`` through
``scripts/report_issue.py``; ``--snapshot`` prints the live state in the baseline's shape, for
a decision PR that changes a guardrail on purpose. The exit code is non-zero only when the
baseline itself cannot be read.
"""

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from scripts.report_issue import IssueTracker, Runner, keep_report, run_gh

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".github" / "guardrails.json"
REPORT_TITLE = "Guardrail drift"
REPORT_LABEL = "guardrail-drift"

REPOSITORY_KEYS = (
    "visibility",
    "allow_merge_commit",
    "allow_rebase_merge",
    "allow_squash_merge",
    "allow_auto_merge",
    "delete_branch_on_merge",
)
SECURITY_KEYS = (
    "secret_scanning",
    "secret_scanning_push_protection",
    "secret_scanning_non_provider_patterns",
    "secret_scanning_validity_checks",
    "dependabot_security_updates",
)
ORGANISATION_KEYS = (
    "two_factor_requirement_enabled",
    "default_repository_permission",
    "members_can_create_repositories",
    "members_can_create_public_repositories",
    "members_can_create_private_repositories",
)
ACTIONS_KEYS = ("enabled", "allowed_actions", "sha_pinning_required")
WORKFLOW_KEYS = ("default_workflow_permissions", "can_approve_pull_request_reviews")

Json = Any
"""A value parsed from JSON."""


class UnreadableError(Exception):
    """A section of the live state is missing or cannot be parsed."""


def _rule(rule: Mapping[str, Json]) -> dict[str, Json]:
    """One ruleset rule, with lists that GitHub returns in any order sorted."""
    parameters = dict(rule.get("parameters") or {})
    checks = parameters.get("required_status_checks")
    if isinstance(checks, list):
        parameters["required_status_checks"] = sorted(checks, key=lambda c: c.get("context", ""))
    for key in ("allowed_merge_methods",):
        if isinstance(parameters.get(key), list):
            parameters[key] = sorted(parameters[key])
    return (
        {"type": rule["type"], "parameters": parameters} if parameters else {"type": rule["type"]}
    )


def normalise_rulesets(rulesets: Iterable[Mapping[str, Json]]) -> dict[str, Json]:
    """The rulesets by name, without ids, links and timestamps."""
    found = {}
    for ruleset in rulesets:
        actors = ruleset.get("bypass_actors")
        found[str(ruleset["name"])] = {
            "target": ruleset.get("target"),
            "enforcement": ruleset.get("enforcement"),
            "conditions": ruleset.get("conditions"),
            # None: the token cannot see who may bypass; the audit reports that, not drift.
            "bypass_actors": None
            if actors is None
            else sorted(
                (
                    {
                        "actor_type": a.get("actor_type"),
                        "actor_id": a.get("actor_id"),
                        "bypass_mode": a.get("bypass_mode"),
                    }
                    for a in actors
                ),
                key=lambda a: (str(a["actor_type"]), str(a["actor_id"])),
            ),
            "rules": {
                str(rule["type"]): _rule(rule).get("parameters", {})
                for rule in ruleset.get("rules") or []
            },
        }
    return found


def normalise_repository(repository: Mapping[str, Json]) -> dict[str, Json]:
    """The merge settings and the status of each security feature."""
    found = {key: repository.get(key) for key in REPOSITORY_KEYS}
    security = repository.get("security_and_analysis") or {}
    found["security"] = {key: (security.get(key) or {}).get("status") for key in SECURITY_KEYS}
    return found


def normalise_environments(environments: Iterable[Mapping[str, Json]]) -> dict[str, Json]:
    """Each environment's branch policy and the branches or tags it allows."""
    return {
        str(env["name"]): {
            "deployment_branch_policy": env.get("deployment_branch_policy"),
            "branches": sorted(
                (f"{b['type']}:{b['name']}" for b in env.get("branches") or []),
            ),
        }
        for env in environments
    }


def normalise_installations(installations: Iterable[Mapping[str, Json]]) -> dict[str, Json]:
    """Each installed app's repository selection and permissions."""
    return {
        str(app["app_slug"]): {
            "repository_selection": app.get("repository_selection"),
            "permissions": dict(sorted((app.get("permissions") or {}).items())),
        }
        for app in installations
    }


def _pick(source: Mapping[str, Json], keys: Sequence[str]) -> dict[str, Json]:
    return {key: source.get(key) for key in keys}


SECTIONS: dict[str, tuple[str, Callable[[Json], Json]]] = {
    "rulesets": ("rulesets", normalise_rulesets),
    "repository": ("repository", normalise_repository),
    "actions": ("actions", lambda value: _pick(value, ACTIONS_KEYS)),
    "workflow_token": ("actions_workflow", lambda value: _pick(value, WORKFLOW_KEYS)),
    "environments": ("environments", normalise_environments),
    "collaborators": (
        "collaborators",
        lambda value: {str(c["login"]): c.get("role_name") for c in value},
    ),
    "organisation": ("organisation", lambda value: _pick(value, ORGANISATION_KEYS)),
    "installations": ("installations", normalise_installations),
}
"""Each baseline section, the live file it comes from and how it is normalised."""


def read_live(directory: Path, name: str) -> Json:
    """Read one live section, or raise :class:`UnreadableError`."""
    try:
        return json.loads((directory / f"{name}.json").read_text())
    except (OSError, ValueError) as error:
        raise UnreadableError(name) from error


def snapshot(directory: Path) -> dict[str, Json]:
    """The live state in the baseline's shape; unreadable sections are left out."""
    found: dict[str, Json] = {}
    for section, (name, normalise) in SECTIONS.items():
        try:
            found[section] = normalise(read_live(directory, name))
        except (UnreadableError, KeyError, TypeError, AttributeError):
            continue
    return found


def differences(expected: Json, found: Json, path: str) -> list[str]:
    """Every place where ``found`` differs from ``expected``, as ``path: expected …, found …``."""
    if isinstance(expected, dict) and isinstance(found, dict):
        lines = []
        for key in sorted(set(expected) | set(found), key=str):
            where = f"{path}/{key}"
            if key not in found:
                lines.append(f"`{where}`: expected `{json.dumps(expected[key])}`, missing")
            elif key not in expected:
                lines.append(f"`{where}`: not in the baseline, found `{json.dumps(found[key])}`")
            else:
                lines.extend(differences(expected[key], found[key], where))
        return lines
    if expected != found:
        return [f"`{path}`: expected `{json.dumps(expected)}`, found `{json.dumps(found)}`"]
    return []


def hidden(expected: Json, found: Json, path: str) -> list[str]:
    """The paths the baseline sets but the live state shows as null: not seen, not changed."""
    if isinstance(expected, dict) and isinstance(found, dict):
        return [
            where
            for key in sorted(set(expected) & set(found), key=str)
            for where in hidden(expected[key], found[key], f"{path}/{key}")
        ]
    return [path] if found is None and expected is not None else []


def _blank(value: Json, paths: Iterable[str], root: str) -> Json:
    """``value`` with every path in ``paths`` set to null, so it is not compared."""
    for path in paths:
        keys = path[len(root) + 1 :].split("/")
        target = value
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = None
    return value


def unexpected_bypasses(
    bypasses: Iterable[Mapping[str, Json]], expected: Iterable[Mapping[str, str]]
) -> list[Mapping[str, Json]]:
    """The bypasses that no ``{actor, ref_prefix}`` entry of the baseline expects."""
    allowed = [(str(e["actor"]), str(e["ref_prefix"])) for e in expected]
    return [
        b
        for b in bypasses
        if not any(
            b.get("actor_name") == actor and str(b.get("ref", "")).startswith(prefix)
            for actor, prefix in allowed
        )
    ]


class Findings:
    """What the audit found: drifted values, unreadable sections and unexpected bypasses."""

    def __init__(self) -> None:
        """Start empty."""
        self.drift: list[str] = []
        self.unreadable: list[str] = []
        self.bypasses: list[Mapping[str, Json]] = []

    def __bool__(self) -> bool:
        """Whether anything was found."""
        return bool(self.drift or self.unreadable or self.bypasses)


def audit(baseline: Mapping[str, Json], directory: Path) -> Findings:
    """Compare every section of the baseline with the live files in ``directory``."""
    findings = Findings()
    live = snapshot(directory)
    for section, (name, _) in SECTIONS.items():
        if section not in live:
            findings.unreadable.append(name)
        elif section in baseline:
            unseen = hidden(baseline[section], live[section], section)
            findings.unreadable.extend(unseen)
            expected = _blank(json.loads(json.dumps(baseline[section])), unseen, section)
            findings.drift.extend(differences(expected, live[section], section))
    try:
        bypasses = read_live(directory, "bypasses")
        findings.bypasses = unexpected_bypasses(bypasses, baseline.get("expected_bypasses", []))
    except UnreadableError:
        findings.unreadable.append("bypasses")
    return findings


def render(findings: Findings, today: date) -> str:
    """The report as Markdown, for the step summary and the issue."""
    lines = [f"# {REPORT_TITLE} {today.isoformat()}", ""]
    if not findings:
        return "\n".join([*lines, "Every guardrail matches `.github/guardrails.json`.", ""])
    if findings.drift:
        lines += ["## Settings that differ from the baseline", ""]
        lines += [f"- {line}" for line in findings.drift]
        lines.append("")
    if findings.bypasses:
        lines += ["## Bypasses of a ruleset in the last day", ""]
        lines += [
            f"- `{b.get('actor_name')}` on `{b.get('ref')}` at {b.get('pushed_at')}"
            for b in findings.bypasses
        ]
        lines.append("")
    if findings.unreadable:
        lines += ["## What could not be read or seen (the audit fails closed)", ""]
        lines += [f"- `{name}`" for name in findings.unreadable]
        lines.append("")
    lines += [
        "If a change was intended, change `.github/guardrails.json` in a decision PR "
        "(`python3 -m scripts.guardrail_audit --snapshot DIR` prints the live state). "
        "Otherwise put the setting back.",
        "",
        "Generated by `scripts/guardrail_audit.py` (ADR 0109).",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, run: Runner = run_gh) -> int:
    """Print the report (and keep it as an issue), or print a snapshot of the live state."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", type=Path, required=True, help="the files of guardrail-fetch.sh")
    parser.add_argument("--baseline", type=Path, default=BASELINE, help="the expected state")
    parser.add_argument(
        "--snapshot", action="store_true", help="print the live state in the baseline's shape"
    )
    parser.add_argument(
        "--post", action="store_true", help="keep the report as the issue 'Guardrail drift <date>'"
    )
    parser.add_argument("--dry-run", action="store_true", help="with --post: write nothing")
    parser.add_argument(
        "--today", type=date.fromisoformat, default=datetime.now(UTC).date(), help="report date"
    )
    args = parser.parse_args(argv)

    if args.snapshot:
        print(json.dumps(snapshot(args.live), indent=2, sort_keys=True))
        return 0
    try:
        baseline = json.loads(args.baseline.read_text())
    except (OSError, ValueError) as error:
        print(f"::error::the baseline {args.baseline} cannot be read: {error}")
        return 2
    findings = audit(baseline, args.live)
    text = render(findings, args.today)
    print(text, end="")
    if args.post:
        tracker = IssueTracker(run)
        tracker.colour = "b60205"
        keep_report(
            tracker,
            tracker.open_issues(),
            REPORT_TITLE,
            REPORT_LABEL,
            "A guardrail differs from .github/guardrails.json (ADR 0109)",
            text,
            bool(findings),
            args.today,
            args.dry_run,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
