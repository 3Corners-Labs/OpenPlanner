"""The file edits and checks of a release, so that the release workflow stays thin (ADR 0079).

``.github/workflows/release.yml`` calls one subcommand per step and does the git and GitHub
side itself (branch, push, pull request, tag). Everything that touches a file or decides a
number is here and tested in ``tests/scripts/test_release_pr.py``:

- ``plan [--version X.Y.Z] [--base release/X.Y]``: the number (given, or what
  ``scripts/release_plan.py`` gives; on a maintenance line always a patch of that line, ADR
  0116 point 3), the previous release, the PR's base, branch and title, and whether the
  release needs the audit of ADR 0078 (a minor or major version, ``X.Y.0``). Printed as
  ``key=value`` lines for ``$GITHUB_OUTPUT``.
- ``prepare --version X.Y.Z --body FILE [--base release/X.Y]``: sets the version in
  ``pyproject.toml`` and ``uv.lock``, the version and the date in ``publiccode.yml`` (ADR 0054),
  moves *Unreleased* in ``CHANGELOG.md`` under ``## X.Y.Z (date)``, takes over the released
  sections of every maintenance line on ``origin`` that ``main`` lacks, and writes the pull
  request body, the PR template filled in, to ``FILE``.
- ``audit --body FILE --findings FILE``: puts the audit's findings into the body under
  ``## Release audit``, ending with ``Verdict: open``. Changing that line to ``Verdict: pass``
  stays the product owner's act (ADR 0078 point 4).
- ``tag-check --version X.Y.Z [--ref release/X.Y]``: finds the squash-merged release PR on
  ``origin/main`` (or on the version's own line), checks that
  ``pyproject.toml`` at that commit says the version and that the tag does not exist yet, and
  prints ``commit=``, ``number=`` and the tag ``message=`` (who dispatched, when) for the tag
  step (ADR 0052 points 3 and 4).

Nothing here pushes, opens or tags; every subcommand can be run on a laptop as well.
"""

import argparse
import re
import subprocess  # noqa: S404 - only for git's CalledProcessError
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from scripts.adr_tables import overview_rows
from scripts.release_plan import (
    MAIN,
    LineError,
    Target,
    git,
    last_release,
    messages_since,
    migrations_changed_since,
    parse_version,
)

_PYPROJECT_VERSION = re.compile(r'^(version = ")[^"]+(")$', re.M)
_LOCK_VERSION = re.compile(r'(\[\[package\]\]\nname = "openplanner"\nversion = ")[^"]+(")')
_UNRELEASED = re.compile(r"^## Unreleased\n", re.M)
_PUBLICCODE_VERSION = re.compile(r'^(softwareVersion: ")[^"]+(")$', re.M)
_PUBLICCODE_DATE = re.compile(r'^(releaseDate: ")[^"]+(")$', re.M)
_AUDIT_SECTION = re.compile(r"^## Release audit\n.*?(?=^## |\Z)", re.M | re.S)
_HEADING = re.compile(r"^#+\s+(.*)$", re.M)
_SECTION = re.compile(r"^## (\d+\.\d+\.\d+) \([^)]*\)\n", re.M)

AUDIT_HEADING = "## Release audit"
VERDICT_OPEN = "Verdict: open"
"""The audit section's last line until the product owner changes it to ``Verdict: pass``."""


def needs_audit(version: str) -> bool:
    """Return whether ``version`` is a minor or major release, which needs the audit of ADR 0078."""
    return parse_version(version)[2] == 0


def plan(
    previous: str,
    messages: Sequence[str],
    requested: str | None,
    migrations_changed: bool = False,
    target: Target = MAIN,
) -> str:
    """Return the version to release.

    Args:
        previous: The last release tag, ``vX.Y.Z``.
        messages: The commit messages since that release.
        requested: A version the product owner typed, or ``None`` for the rules of ADR 0052.
        migrations_changed: Whether ``migrations/versions/`` changed since that release.
        target: ``main`` or the maintenance line the release is made on (ADR 0116 point 3).

    Returns:
        ``requested`` when it is a version above the last release, else the version the
        commits call for (MINOR at least when a migration changed, ADR 0116 point 4). On a
        line always a patch of that line.

    Raises:
        ValueError: ``requested`` is no version or not above the last release, is a patch
            although a migration changed, is not a patch of the line, or nothing was merged
            since the last release.
    """
    last = previous.removeprefix("v")
    if requested and not target.admits(requested):
        raise LineError(f"{requested} is not a patch of line {target.line}")
    if requested:
        if parse_version(requested) <= parse_version(last):
            raise ValueError(f"{requested} is not above the last release {previous}")
        if migrations_changed and parse_version(requested)[2] != 0:
            raise ValueError(
                f"{requested} is a patch, but a migration changed since {previous}; "
                "a patch carries no migration (ADR 0116 point 4)"
            )
        return requested
    proposed = target.next_version(last, messages, migrations_changed)
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


def merge_changelogs(text: str, others: Sequence[str]) -> str:
    """Return ``CHANGELOG.md`` with the released sections of ``others`` it lacks, in order.

    A line's patch releases have their own sections in the line's ``CHANGELOG.md``; the next
    release PR on ``main`` takes them over (ADR 0116 point 3). Sections are ordered by version,
    newest first, under ``## Unreleased``; a section ``main`` already has is left as it is.
    """
    head, sections = _changelog_sections(text)
    for other in others:
        for version, body in _changelog_sections(other)[1].items():
            sections.setdefault(version, body)
    ordered = sorted(sections, key=parse_version, reverse=True)
    return (head + "".join(sections[version] for version in ordered)).rstrip("\n") + "\n"


def _changelog_sections(text: str) -> tuple[str, dict[str, str]]:
    """Return the text before the first released section, and the sections by version."""
    matches = list(_SECTION.finditer(text))
    if not matches:
        return text, {}
    bounds = [m.start() for m in matches] + [len(text)]
    sections = {
        match[1]: text[bounds[i] : bounds[i + 1]].rstrip("\n") + "\n\n"
        for i, match in enumerate(matches)
    }
    return text[: matches[0].start()], sections


def adr_progress(before: str, after: str, previous: str) -> str:
    """Return the release PR's ADR section: what became built, and what is still open (ADR 0119).

    Args:
        before: ``docs/adr-status.md`` at the last release.
        after: ``docs/adr-status.md`` now.
        previous: The last release tag, for the wording.

    Returns:
        A Markdown section; the product owner reads here which decisions this release finishes.
    """
    was = {row.number: row.category for row in overview_rows(before)}
    now = {row.number: (row.title, row.category) for row in overview_rows(after)}
    done = [
        f"- ADR {number} {title}"
        for number, (title, word) in sorted(now.items())
        if word == "BUILT" and was.get(number) != "BUILT"
    ]
    open_ = sorted(n for n, (_, word) in now.items() if word in {"PARTIAL", "NOT BUILT"})
    unbuilt = sorted(n for n, (_, word) in now.items() if word == "NOT BUILT")
    return (
        "## ADRs\n\n"
        f"Built since {previous} ({len(done)}):\n\n" + ("\n".join(done) or "- (none)") + "\n\n"
        f"Still open ({len(open_)}): {', '.join(open_) or 'none'}; of these nothing built yet: "
        f"{', '.join(unbuilt) or 'none'}. Source: `docs/adr-status.md`, tested against every "
        "ADR's *Build status* table.\n\n"
    )


def pr_body(
    version: str,
    previous: str,
    subjects: Sequence[str],
    actor: str,
    date: str,
    adrs: str,
) -> str:
    """Return the release PR's description: the PR template with every section answered.

    Args:
        version: The version released, ``X.Y.Z``.
        previous: The last release tag, ``vX.Y.Z``.
        subjects: The subjects of the commits since the last release.
        actor: Who dispatched the workflow; the instruction of ADR 0012 and 0052.
        date: The date in the changelog heading.
        adrs: The ADR section of :func:`adr_progress`; empty when the overview at the last
            release could not be read.
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
        f"Commits since {previous} ({len(subjects)}):\n\n{listed}\n\n" + adrs + "## Why\n\n"
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
    target = Target.from_ref(args.base)
    version = plan(
        previous,
        messages_since(previous),
        args.version or None,
        migrations_changed_since(previous),
        target,
    )
    for key, value in (
        ("version", version),
        ("previous", previous),
        # A dry run from a feature branch plans as main does; its PR would go to that branch
        # (nothing is opened), while `tag-check` from it looks on origin/main (Target.branch).
        ("base", target.branch if target.line else args.base),
        ("branch", f"release/v{version}"),
        ("title", f"chore(release): v{version}"),
        ("audit", "true" if needs_audit(version) else "false"),
    ):
        sys.stdout.write(f"{key}={value}\n")


def _at(ref: str, path: Path) -> str:
    """Return a file's text at a git ref; empty when it did not exist there."""
    try:
        return git("show", f"{ref}:{path.as_posix()}")
    except subprocess.CalledProcessError:
        return ""


def _prepare(args: argparse.Namespace) -> None:
    version, date = args.version, args.date or _today()
    parse_version(version)
    previous = last_release()
    subjects = [message.splitlines()[0] for message in messages_since(previous)]
    for name, edit in (
        ("pyproject.toml", bump_pyproject),
        ("uv.lock", bump_lock),
        ("publiccode.yml", lambda text, v: bump_publiccode(text, v, date)),
        (
            "CHANGELOG.md",
            lambda text, v: merge_changelogs(
                release_changelog(text, v, date), _line_changelogs(Target.from_ref(args.base))
            ),
        ),
    ):
        path = Path(name)
        path.write_text(edit(path.read_text(), version))
    overview = Path("docs/adr-status.md")
    before = _at(previous, overview)
    # Without the overview at the last release the list would claim every ADR: leave it out.
    adrs = (
        adr_progress(before, overview.read_text(), previous) if before and overview.exists() else ""
    )
    Path(args.body).write_text(pr_body(version, previous, subjects, args.actor, date, adrs))
    sys.stdout.write(f"prepared v{version} ({date}); body in {args.body}\n")


def _line_changelogs(target: Target) -> list[str]:
    """Return ``CHANGELOG.md`` of every maintenance line on ``origin`` (ADR 0116 point 3).

    Only for a release on ``main``: a line's file already holds its own sections and takes no
    other line's.
    """
    if target.line is not None:
        return []
    refs = git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/release/")
    return [git("show", f"{ref}:CHANGELOG.md") for ref in refs.split() if Target.from_ref(ref).line]


def _audit(args: argparse.Namespace) -> None:
    body = Path(args.body)
    body.write_text(with_audit(body.read_text(), Path(args.findings).read_text()))
    sys.stdout.write(f"audit section written to {args.body}; {VERDICT_OPEN}\n")


def _tag_check(args: argparse.Namespace) -> None:
    version = args.version
    parse_version(version)
    if git("tag", "--list", f"v{version}").strip():
        raise ValueError(f"tag v{version} exists already; a release is never re-tagged")
    target = Target.from_ref(args.ref)
    if not target.admits(version):
        raise LineError(f"{version} is not a patch of line {target.line}; it is not tagged there")
    sha, number = release_commit(version, f"origin/{target.branch}")
    message = tag_message(version, args.actor, args.date or _today())
    sys.stdout.write(f"commit={sha}\nnumber={number}\nmessage={message}\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand; print the reason and return 1 when the release cannot go ahead."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan", help="the number, branch, title and audit need")
    plan_parser.add_argument("--version", default="", help="X.Y.Z; empty for the rules")
    plan_parser.add_argument(
        "--base", default="main", help="where the release PR goes: main or a line release/X.Y"
    )
    prepare = commands.add_parser("prepare", help="edit the three files and write the PR body")
    prepare.add_argument("--version", required=True)
    prepare.add_argument(
        "--base", default="main", help="where the release PR goes: main or a line release/X.Y"
    )
    prepare.add_argument("--body", required=True, help="where to write the PR body")
    prepare.add_argument("--actor", default="the product owner", help="who dispatched")
    prepare.add_argument("--date", default="", help="YYYY-MM-DD; default today (UTC)")
    audit = commands.add_parser("audit", help="put the findings into the PR body")
    audit.add_argument("--body", required=True)
    audit.add_argument("--findings", required=True, help="the audit's output")
    tag_check = commands.add_parser("tag-check", help="find and check the merged release")
    tag_check.add_argument("--version", required=True)
    tag_check.add_argument(
        "--ref", default="main", help="the dispatched branch: main or a line release/X.Y"
    )
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
