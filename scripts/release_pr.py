"""The file edits and checks of a release, so that the release workflow stays thin (ADR 0079).

``.github/workflows/release.yml`` calls one subcommand per step and does the git and GitHub
side itself (branch, push, pull request, tag). Everything that touches a file or decides a
number is here and tested in ``tests/scripts/test_release_pr.py``:

- ``plan [--version X.Y.Z]``: the number (given, or what ``scripts/release_plan.py`` gives),
  the previous release, the branch and title, and whether the release needs the audit of
  ADR 0078 (a minor or major version, ``X.Y.0``). Printed as ``key=value`` lines for
  ``$GITHUB_OUTPUT``.
- ``prepare --version X.Y.Z --body FILE``: sets the version in ``pyproject.toml`` and
  ``uv.lock``, the version and the date in ``publiccode.yml`` (ADR 0054), moves *Unreleased*
  in ``CHANGELOG.md`` under ``## X.Y.Z (date)`` and writes the pull request body, the PR
  template filled in, to ``FILE``.
- ``audit --body FILE --findings FILE``: puts the audit's findings into the body under
  ``## Release audit``, ending with ``Verdict: open``. Changing that line to ``Verdict: pass``
  stays the product owner's act (ADR 0078 point 4).
- ``tag-check --version X.Y.Z``: finds the squash-merged release PR on ``main``, checks that
  ``pyproject.toml`` at that commit says the version and that the tag does not exist yet, and
  prints ``commit=``, ``number=`` and the tag ``message=`` (who dispatched, when) for the tag
  step (ADR 0052 points 3 and 4).

Nothing here pushes, opens or tags; every subcommand can be run on a laptop as well.
"""

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from scripts.release_plan import git, last_release, messages_since, next_version

VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_PYPROJECT_VERSION = re.compile(r'^(version = ")[^"]+(")$', re.M)
_LOCK_VERSION = re.compile(r'(\[\[package\]\]\nname = "openplanner"\nversion = ")[^"]+(")')
_UNRELEASED = re.compile(r"^## Unreleased\n", re.M)
_PUBLICCODE_VERSION = re.compile(r'^(softwareVersion: ")[^"]+(")$', re.M)
_PUBLICCODE_DATE = re.compile(r'^(releaseDate: ")[^"]+(")$', re.M)
_AUDIT_SECTION = re.compile(r"^## Release audit\n.*?(?=^## |\Z)", re.M | re.S)
_HEADING = re.compile(r"^#+\s+(.*)$", re.M)

AUDIT_HEADING = "## Release audit"
VERDICT_OPEN = "Verdict: open"
"""The audit section's last line until the product owner changes it to ``Verdict: pass``."""


def parse_version(version: str) -> tuple[int, int, int]:
    """Return ``X.Y.Z`` as numbers, or raise ``ValueError`` when it is not a version."""
    found = VERSION.match(version)
    if not found:
        raise ValueError(f"{version!r} is not a version X.Y.Z")
    major, minor, patch = (int(part) for part in found.groups())
    return major, minor, patch


def needs_audit(version: str) -> bool:
    """Return whether ``version`` is a minor or major release, which needs the audit of ADR 0078."""
    return parse_version(version)[2] == 0


def plan(previous: str, messages: Sequence[str], requested: str | None) -> str:
    """Return the version to release.

    Args:
        previous: The last release tag, ``vX.Y.Z``.
        messages: The commit messages since that release.
        requested: A version the product owner typed, or ``None`` for the rules of ADR 0052.

    Returns:
        ``requested`` when it is a version above the last release, else the version the
        commits call for.

    Raises:
        ValueError: ``requested`` is no version or not above the last release, or nothing was
            merged since the last release.
    """
    last = previous.removeprefix("v")
    if requested:
        if parse_version(requested) <= parse_version(last):
            raise ValueError(f"{requested} is not above the last release {previous}")
        return requested
    proposed = next_version(last, messages)
    if proposed is None:
        raise ValueError(f"nothing merged since {previous}; nothing to release")
    return proposed


def bump_pyproject(text: str, version: str) -> str:
    """Return ``pyproject.toml`` with ``project.version`` set to ``version``."""
    text, count = _PYPROJECT_VERSION.subn(rf"\g<1>{version}\g<2>", text, count=1)
    if count != 1:
        raise ValueError("pyproject.toml has no `version = ...` line")
    return text


def bump_lock(text: str, version: str) -> str:
    """Return ``uv.lock`` with the project's own version set to ``version``.

    Only the ``openplanner`` package line changes, as ``uv lock`` would do it; CI's
    ``uv lock --check`` fails if the result disagrees with ``pyproject.toml``.
    """
    text, count = _LOCK_VERSION.subn(rf"\g<1>{version}\g<2>", text, count=1)
    if count != 1:
        raise ValueError("uv.lock has no package entry for openplanner")
    return text


def bump_publiccode(text: str, version: str, date: str) -> str:
    """Return ``publiccode.yml`` with ``softwareVersion`` and ``releaseDate`` set (ADR 0054).

    Catalogues such as developer.overheid.nl read both; the rest of the file is left as it is,
    comments included, so no YAML library rewrites it.
    """
    text, versions = _PUBLICCODE_VERSION.subn(rf"\g<1>{version}\g<2>", text, count=1)
    text, dates = _PUBLICCODE_DATE.subn(rf"\g<1>{date}\g<2>", text, count=1)
    if versions != 1 or dates != 1:
        raise ValueError(
            'publiccode.yml needs a `softwareVersion: "..."` and a `releaseDate: "..."` line'
        )
    return text


def release_changelog(text: str, version: str, date: str) -> str:
    """Return ``CHANGELOG.md`` with *Unreleased* released as ``version`` on ``date``.

    The heading ``## Unreleased`` becomes ``## X.Y.Z (date)`` and a fresh, empty
    ``## Unreleased`` goes above it, as the runbook describes.
    """
    text, count = _UNRELEASED.subn(f"## Unreleased\n\n## {version} ({date})\n", text, count=1)
    if count != 1:
        raise ValueError("CHANGELOG.md has no `## Unreleased` heading")
    return text


def pr_body(version: str, previous: str, subjects: Sequence[str], actor: str, date: str) -> str:
    """Return the release PR's description: the PR template with every section answered.

    Args:
        version: The version released, ``X.Y.Z``.
        previous: The last release tag, ``vX.Y.Z``.
        subjects: The subjects of the commits since the last release.
        actor: Who dispatched the workflow; the instruction of ADR 0012 and 0052.
        date: The date in the changelog heading.
    """
    audit = needs_audit(version)
    listed = "\n".join(f"- {subject}" for subject in subjects) or "- (none)"
    audit_section = (
        f"{AUDIT_HEADING}\n\n"
        "Pending: the `audit` job of the release workflow runs `scripts/release-audit.sh` "
        "(the Codex CLI over everything since the last release, ADR 0078 point 4) and writes "
        "its findings here. The product owner reads them and changes the verdict on the last "
        "line from *open* to *pass*; `main.yml` refuses the tag until then.\n\n"
        f"{VERDICT_OPEN}\n\n"
        if audit
        else ""
    )
    return (
        "## What\n\n"
        f"Release v{version}: `pyproject.toml`, `uv.lock` and `publiccode.yml` say `{version}`, "
        f"and *Unreleased* in `CHANGELOG.md` is now `## {version} ({date})`. Opened by the "
        "`release` workflow on "
        f"the dispatch of {actor} (ADR 0079); nothing else changes.\n\n"
        f"Commits since {previous} ({len(subjects)}):\n\n{listed}\n\n"
        "## Why\n\n"
        "A release is a release PR plus a tag on the product owner's instruction (ADR 0052 "
        "point 3). This PR is the first half; the `tag` dispatch of the same workflow sets the "
        "tag on its merge commit, and `main.yml` builds, signs and publishes.\n\n"
        "## Assumptions\n\n"
        f"The number {version} follows the Conventional Commits since {previous} "
        "(ADR 0052 point 2), or was typed by the dispatcher.\n\n"
        "## Review questions\n"
        "1. **Which endpoint or job is new or changed, and is `require_scope` on it?** None; "
        "the release workflow opened this PR, no application code changes.\n"
        "2. **Which input comes from outside, and where is it validated?** The `version` input "
        "of the dispatch, checked by `scripts/release_pr.py` (`X.Y.Z`, above the last release).\n"
        "3. **Can PII reach a log, error message, URL or external payload here?** No: version "
        "numbers and commit subjects only.\n"
        "4. **Which test would fail if this change were wrong?** "
        "`tests/scripts/test_release_pr.py`; in CI `uv lock --check` fails when `uv.lock` "
        "disagrees with `pyproject.toml`, and `main.yml` refuses a tag that disagrees with "
        "either.\n"
        "5. **Is a dependency added, and why?** No.\n"
        "6. **Which settings does this add or change?** None; no tunable value.\n"
        "7. **Which admin screen or setting is new or moved?** None.\n\n"
        "## Security review\n\n"
        "Not applicable: four files change, each by a tested edit; the independent audit "
        + ("is the section below.\n\n" if audit else "is not needed for a patch release.\n\n")
        + audit_section
        + "## How tested\n\n"
        "CI on this PR (`make check` in the fast lane, the full lane in the merge queue); the "
        "release workflow's own tests in `tests/scripts/`.\n\n"
        "## Checklist\n"
        "- [x] I agree to the OpenPlanner Contributor Licence Agreement (`docs/cla.md`)\n"
        "- [ ] `make check` green (CI reports it on this PR)\n"
        "- [x] Label `ci:full` not needed: no authorisation, navigation, template or widget "
        "change\n"
        "- [x] Auto-merge enabled; the *decision gate* is red until the product owner sets the "
        "label `po:approved`, because a release PR is a decision (ADR 0078)\n"
        "- [x] Screenshots: no UI change\n"
        "- [x] i18n: no text change\n"
        "- [x] Manual: no admin screen change\n"
        "- [x] Migration reversible: n.a.\n"
        "- [x] Docs: the changelog is this change\n"
        "- [x] ADR build status: ADR 0079 (the release workflow) says what runs\n\n"
        "🤖 Generated with the `release` workflow (`.github/workflows/release.yml`)\n"
    )


def with_audit(body: str, findings: str) -> str:
    """Return ``body`` with ``findings`` under ``## Release audit``, ending with ``Verdict: open``.

    An existing section is replaced; headings inside the findings become bold lines, so the
    section stays one section and a second run replaces it whole.
    """
    text = _HEADING.sub(r"**\1**", findings.strip()) or "The audit produced no output."
    section = f"{AUDIT_HEADING}\n\n{text}\n\n{VERDICT_OPEN}\n\n"
    body, count = _AUDIT_SECTION.subn(lambda _: section, body, count=1)
    if count == 0:
        body = body.rstrip("\n") + "\n\n" + section
    return body


def tag_message(version: str, actor: str, date: str) -> str:
    """Return the annotated tag's message: who gave the instruction and when (ADR 0052 point 3)."""
    return f"v{version}: tagged on the instruction of {actor} on {date}"


def release_commit(version: str, branch: str = "origin/main") -> tuple[str, str]:
    """Return the merge commit and PR number of ``chore(release): vX.Y.Z`` on ``branch``.

    Raises:
        ValueError: The release PR is not merged on ``branch``, or ``pyproject.toml`` at that
            commit says another version.
    """
    pattern = re.compile(rf"^chore\(release\): v{re.escape(version)} \(#(\d+)\)$")
    for line in git("log", branch, "--first-parent", "--format=%H %s").splitlines():
        sha, _, subject = line.partition(" ")
        if found := pattern.match(subject):
            break
    else:
        raise ValueError(f"no merged release PR `chore(release): v{version}` on {branch}")
    project = _PYPROJECT_VERSION.search(git("show", f"{sha}:pyproject.toml"))
    if not project or project.group(0) != f'version = "{version}"':
        raise ValueError(f"pyproject.toml at {sha[:12]} does not say {version}")
    return sha, found.group(1)


def _today() -> str:
    """Return today's date in UTC as ``YYYY-MM-DD``."""
    return datetime.now(UTC).date().isoformat()


def _plan(args: argparse.Namespace) -> None:
    previous = last_release()
    version = plan(previous, messages_since(previous), args.version or None)
    for key, value in (
        ("version", version),
        ("previous", previous),
        ("branch", f"release/v{version}"),
        ("title", f"chore(release): v{version}"),
        ("audit", "true" if needs_audit(version) else "false"),
    ):
        sys.stdout.write(f"{key}={value}\n")


def _prepare(args: argparse.Namespace) -> None:
    version, date = args.version, args.date or _today()
    parse_version(version)
    previous = last_release()
    subjects = [message.splitlines()[0] for message in messages_since(previous)]
    for name, edit in (
        ("pyproject.toml", bump_pyproject),
        ("uv.lock", bump_lock),
        ("publiccode.yml", lambda text, v: bump_publiccode(text, v, date)),
        ("CHANGELOG.md", lambda text, v: release_changelog(text, v, date)),
    ):
        path = Path(name)
        path.write_text(edit(path.read_text(), version))
    Path(args.body).write_text(pr_body(version, previous, subjects, args.actor, date))
    sys.stdout.write(f"prepared v{version} ({date}); body in {args.body}\n")


def _audit(args: argparse.Namespace) -> None:
    body = Path(args.body)
    body.write_text(with_audit(body.read_text(), Path(args.findings).read_text()))
    sys.stdout.write(f"audit section written to {args.body}; {VERDICT_OPEN}\n")


def _tag_check(args: argparse.Namespace) -> None:
    version = args.version
    parse_version(version)
    if git("tag", "--list", f"v{version}").strip():
        raise ValueError(f"tag v{version} exists already; a release is never re-tagged")
    sha, number = release_commit(version, args.branch)
    message = tag_message(version, args.actor, args.date or _today())
    sys.stdout.write(f"commit={sha}\nnumber={number}\nmessage={message}\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand; print the reason and return 1 when the release cannot go ahead."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan", help="the number, branch, title and audit need")
    plan_parser.add_argument("--version", default="", help="X.Y.Z; empty for the rules")
    prepare = commands.add_parser("prepare", help="edit the three files and write the PR body")
    prepare.add_argument("--version", required=True)
    prepare.add_argument("--body", required=True, help="where to write the PR body")
    prepare.add_argument("--actor", default="the product owner", help="who dispatched")
    prepare.add_argument("--date", default="", help="YYYY-MM-DD; default today (UTC)")
    audit = commands.add_parser("audit", help="put the findings into the PR body")
    audit.add_argument("--body", required=True)
    audit.add_argument("--findings", required=True, help="the audit's output")
    tag_check = commands.add_parser("tag-check", help="find and check the merged release")
    tag_check.add_argument("--version", required=True)
    tag_check.add_argument("--branch", default="origin/main", help="where the release PR merged")
    tag_check.add_argument("--actor", default="the product owner", help="who dispatched")
    tag_check.add_argument("--date", default="", help="YYYY-MM-DD; default today (UTC)")
    args = parser.parse_args(argv)
    try:
        {"plan": _plan, "prepare": _prepare, "audit": _audit, "tag-check": _tag_check}[
            args.command
        ](args)
    except ValueError as error:
        sys.stderr.write(f"::error::{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
