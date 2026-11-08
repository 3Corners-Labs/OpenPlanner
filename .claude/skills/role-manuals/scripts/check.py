"""Check an organisation's role manuals against this checkout of the software.

Usage, from the checkout (the app's own modules and babel come from its environment)::

    uv run python .claude/skills/role-manuals/scripts/check.py <workspace> [--since <commit>]
        [--fix]

Writes ``evidence/check.md`` in the workspace and exits with 1 when a blocking finding is left.
It checks what needs the software's own Python; the sources are parsed once, by ``build.mjs``,
which also refuses a manual that names a term the organisation does not show. Run that first:
it writes ``build/manuals.json`` (per manual its tasks, figures and bold spans, with the hash of
its source), which this check reads and refuses when a source changed since.

1. Tasks: the screen of each task is a help topic of the portal or a participant page, the
   permission exists, and a role of the manual holds it in the authorisation matrix
   (``app.domain.authorization.MATRIX``).
2. Screenshots: every figure and shot of a task is in the shot list (``evidence/shots.json``,
   written by ``capture.mjs``); which files still have to follow.
3. Labels: every bold span the build found is a text of the software (the ``nl`` catalogue, read
   as the app reads it); a path (A → B) is checked part by part.
4. Since a commit: labels the software renamed or removed and where the manuals use them (a
   removed one blocks), help topics and templates that changed. ``--fix`` writes a renamed label
   into the sources, inside bold labels only.
5. Screenshots that differ from the previous capture (``img/previous/``): ``capture.mjs``
   compares them and writes ``evidence/diff.json`` and a picture of each difference in
   ``evidence/diff/``.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from app.domain.authorization import MATRIX, PARTICIPANT_ACTIONS, Permission, Role  # noqa: E402
from app.i18n.po import load_catalogues  # noqa: E402

PARTICIPANT_SCREENS = frozenset(
    {
        "participant:group-page",
        "participant:booking-form",
        "participant:confirm",
        "participant:manage",
        "participant:move",
        "participant:cancel",
        "participant:join",
        "participant:trial",
        "participant:email",
    }
)
"""Participant pages have no help topic; these keys name them."""

DEFAULT_ROLES = {
    "beheerder": [Role.SUPERUSER, Role.ORG_ADMIN, Role.AUDITOR],
    "planner": [Role.GROUP_ADMIN],
    "medewerker": [Role.AGENT],
}
"""Matrix roles per manual, matched on a word in the manual's ``role``; others are participants."""

STRUCTURAL = re.compile(
    r"^(wanneer|resultaat|let op|tip|belangrijk|waarom|stap \d+|voorbeeld|doel|wie|let wel)\b",
    re.IGNORECASE,
)
"""Bold words that structure the text and are no label of the software."""

BOLD_SPAN = re.compile(r"\*\*((?:(?!\n\s*\n).)+?)\*\*", re.DOTALL)
COMMIT = re.compile(r"[0-9a-f]{7,40}")
"""A commit as ``--since`` takes it: an abbreviated or full hash, never an option."""


@dataclass
class Report:
    """Findings per section; ``blocking`` ones make the exit code 1."""

    sections: dict[str, list[str]] = field(default_factory=dict)
    blocking: int = 0

    def add(self, section: str, line: str, *, blocking: bool = False) -> None:
        """Add one finding to a section."""
        self.sections.setdefault(section, []).append(
            ("**Blokkerend:** " if blocking else "") + line
        )
        self.blocking += int(blocking)

    def markdown(self) -> str:
        """Return the report as Markdown."""
        out = ["# Controle van de handleidingen", "", f"Blokkerende bevindingen: {self.blocking}."]
        for name, lines in self.sections.items():
            out += ["", f"## {name}", "", *[f"- {line}" for line in lines]]
        return "\n".join(out) + "\n"


@dataclass
class Workspace:
    """The folders of one organisation's manuals."""

    root: Path

    def __post_init__(self) -> None:
        """Resolve the folders below the root."""
        self.sources = self.root / "sources"
        self.img = self.root / "img"
        self.evidence = self.root / "evidence"


def guard_outside_checkout(path: Path) -> None:
    """Stop when ``path`` lies inside a checkout of the software (same rule as workspace.mjs).

    Symbolic links are followed first, so a link into a checkout is refused as well.
    """
    path = path.resolve()
    for folder in (path, *path.parents):
        if (folder / "app" / "i18n" / "locales" / "nl.po").exists() or (
            folder / ".claude" / "skills" / "role-manuals"
        ).exists():
            sys.exit(
                f"check.py: {path} lies inside the checkout {folder}; keep the workspace outside it"
            )


def git(*args: str) -> str:
    """Run git in the checkout and return its output."""
    exe = shutil.which("git") or "git"
    return subprocess.run(  # noqa: S603
        [exe, "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout


def catalogue_at(commit: str | None) -> dict[str, str]:
    """Return the ``nl`` catalogue of the checkout, or as it was at ``commit``."""
    if commit is None:
        return load_catalogues()["nl"]
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "nl.po").write_text(git("show", f"{commit}:app/i18n/locales/nl.po"))
        return load_catalogues(Path(folder))["nl"]


def normalise(text: str) -> str:
    """Collapse white space and drop closing punctuation, as a label is quoted without it."""
    return re.sub(r"\s+", " ", text).strip().rstrip(".!?:").strip()


def labels_of(bold: str, variables: frozenset[str] = frozenset()) -> list[str]:
    """Return the labels of one bold span; a path (A → B → C) gives its parts.

    The spans are the filled-in text, so a part that is a value of ``variables.json`` (the name
    of the service, say) is the organisation's word, not a text of the software.
    """
    found = []
    for part in re.split(r"\s*→\s*", bold):
        part = normalise(part)
        if not part or part[0] in "[0123456789" or part in variables or bold.strip().endswith(":"):
            continue
        if not STRUCTURAL.match(part):
            found.append(part)
    return found


def commit_hash(value: str) -> str:
    """Return ``value`` when it is a commit hash; anything else (an option, a ref) is refused."""
    if not COMMIT.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is no commit hash")
    return value


def rename_labels(text: str, renamed: dict[str, str]) -> str:
    """Write each renamed label into ``text``: only a whole part of a bold label is replaced."""

    def span(match: re.Match[str]) -> str:
        parts = re.split(r"(\s*→\s*)", match.group(1))
        for i in range(0, len(parts), 2):
            core = normalise(parts[i])
            if core in renamed:
                body = parts[i].strip()
                lead = parts[i][: len(parts[i]) - len(parts[i].lstrip())]
                tail = parts[i][len(parts[i].rstrip()) :]
                punctuation = re.search(r"[.!?:]*$", body).group(0)  # type: ignore[union-attr]
                parts[i] = f"{lead}{renamed[core]}{punctuation}{tail}"
        return "**" + "".join(parts) + "**"

    return BOLD_SPAN.sub(span, text)


class Catalogue:
    """The texts of the software, for exact and placeholder matches."""

    def __init__(self, entries: dict[str, str], extra: list[str]) -> None:
        """Index the texts without their closing punctuation."""
        self.values = {normalise(v) for v in entries.values() if v} | set(extra)
        self.patterns = [
            re.compile("^" + re.sub(r"\\\{\w+\\\}", ".+?", re.escape(v)) + "$")
            for v in self.values
            if "{" in v and len(v) < 300 and len(re.sub(r"\{\w+\}|\W", "", v)) >= 6
        ]

    def match(self, label: str) -> str:
        """Return ``exact``, ``placeholder``, ``in tekst`` or ``onbekend`` for a label."""
        label = normalise(label)
        if label in self.values:
            return "exact"
        if any(p.match(label) for p in self.patterns):
            return "placeholder"
        if "…" in label:
            wildcard = re.compile("^" + re.escape(label).replace("…", ".+?") + "$")
            if any(wildcard.match(v) for v in self.values):
                return "placeholder"
        if any(label in v for v in self.values):
            return "in tekst"
        return "onbekend"


def manual_roles(role: str, config: dict[str, list[str]]) -> list[str] | None:
    """Return the matrix roles of a manual's ``role``, or None for the participants' manual."""
    for word, roles in config.items():
        if word.lower() in role.lower():
            return roles
    return None


def check_tasks(manual: dict, ctx: dict, report: Report) -> None:
    """Check the tasks and figures of one manual (from build/manuals.json)."""
    roles = manual_roles(manual["role"], ctx["roles"])
    for task in manual["tasks"]:
        where = f"{manual['file']}.md:{task['line']}"
        ctx["tasks"] += 1
        screen, permission = task.get("screen", ""), task.get("permission", "")
        if screen not in ctx["topics"] and screen not in PARTICIPANT_SCREENS:
            report.add("Koppelingen", f"{where}: scherm `{screen}` bestaat niet.", blocking=True)
        if permission not in ctx["codes"]:
            report.add("Koppelingen", f"{where}: recht `{permission}` bestaat niet.", blocking=True)
        elif roles is None and permission not in ctx["participant"]:
            report.add(
                "Koppelingen",
                f"{where}: `{permission}` is geen recht van deelnemers.",
                blocking=True,
            )
        elif roles and not any(permission in ctx["rights"].get(r, set()) for r in roles):
            report.add(
                "Koppelingen",
                f"{where}: de rol {manual['role']} heeft `{permission}` niet.",
                blocking=True,
            )
        for shot in task.get("shots", []):
            ctx["used"].add(shot)
            if shot not in ctx["shots"]:
                report.add(
                    "Koppelingen",
                    f"{where}: schermafdruk `{shot}` staat niet in de opnamelijst.",
                    blocking=True,
                )
    for figure in manual["figures"]:
        ctx["used"].add(figure["id"])
        if figure["id"] not in ctx["shots"]:
            report.add(
                "Schermafdrukken",
                f"{manual['file']}.md:{figure['line']}: `{figure['id']}` "
                "staat niet in de opnamelijst.",
                blocking=True,
            )
    if not manual["tasks"] and not manual["file"].startswith("00-"):
        report.add(
            "Koppelingen",
            f"{manual['file']}.md: geen taakkoppelingen (nodig voor contextgedreven hulp).",
        )


def labels(manual: dict, variables: frozenset[str] = frozenset()) -> list[tuple[int, str]]:
    """Return (line, label) for every label in the bold spans of one manual."""
    return [
        (span["line"], label)
        for span in manual["bold"]
        for label in labels_of(span["text"], variables)
    ]


def load_manuals(ws: Workspace, variables: Path) -> list[dict]:
    """Return the manuals of build/manuals.json, or stop when the build is not of these inputs.

    The build is current when it covers exactly the sources there are, every source's bytes hash
    as they did, and so do the bytes of ``variables.json``, which filled in the spans.
    """
    found = ws.root / "build" / "manuals.json"
    if not found.exists():
        sys.exit(f"check.py: {found} is missing; run build.mjs {ws.root} first")
    data = json.loads(found.read_text())
    manuals = data.get("manuals", []) if isinstance(data, dict) else []
    stale = {p.stem for p in ws.sources.glob("*.md")} != {m["file"] for m in manuals} or any(
        hashlib.sha256((ws.sources / f"{m['file']}.md").read_bytes()).hexdigest() != m["sha256"]
        for m in manuals
    )
    if stale or hashlib.sha256(variables.read_bytes()).hexdigest() != data.get("variables_sha256"):
        sys.exit(
            "check.py: the sources or variables.json changed after the last build; build again"
        )
    return manuals


def check_labels(
    manual: dict, catalogue: Catalogue, verdicts: dict, report: Report, variables: frozenset[str]
) -> None:
    """Check that every bold label of a manual is a text of the software."""
    for number, label in labels(manual, variables):
        verdict = catalogue.match(label)
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        where = f"{manual['file']}.md:{number}"
        if verdict == "onbekend":
            report.add("Schermteksten", f"{where}: **{label}** komt niet voor in de software.")
        elif verdict == "in tekst":
            report.add("Schermteksten (deel van een langere tekst)", f"{where}: **{label}**")


def since(
    commit: str,
    sources: dict[Path, str],
    manuals: list[dict],
    fix: bool,
    report: Report,
    variables: frozenset[str],
) -> None:
    """Report what the software changed since ``commit`` and where the manuals use it."""
    old, new = catalogue_at(commit), catalogue_at(None)
    new_values = {normalise(v) for v in new.values() if v}
    renamed = {
        normalise(old[k]): normalise(new[k])
        for k in old
        if k in new
        and normalise(old[k]) != normalise(new[k])
        and normalise(old[k]) not in new_values
    }
    removed = {
        normalise(old[k]) for k in old if k not in new and normalise(old[k]) not in new_values
    }
    section = f"Gewijzigd in de software sinds {commit[:9]}"
    for manual in manuals:
        for number, label in labels(manual, variables):
            core = normalise(label)
            where = f"{manual['file']}.md:{number}"
            if core in renamed:
                action = "vervangen" if fix else "gebruik --fix om te vervangen"
                report.add(section, f"{where}: **{label}** heet nu **{renamed[core]}** ({action}).")
            if core in removed:
                report.add(
                    section,
                    f"{where}: **{label}** bestaat niet meer in de software.",
                    blocking=True,
                )
    for path, text in sources.items():
        if fix and renamed:
            changed = rename_labels(text, renamed)
            if changed != text:
                path.write_text(changed)
                sources[path] = changed
    topics = git("diff", "--name-only", f"{commit}..HEAD", "--", "docs/manual/nl").split()
    for topic in topics:
        slug = Path(topic).stem
        hits = [
            f"{m['file']}.md:{t['line']} › {t['title']}"
            for m in manuals
            for t in m["tasks"]
            if t.get("screen") == slug
        ]
        report.add(
            section,
            f"Hulp bij scherm `{slug}` gewijzigd; "
            f"lees na: {', '.join(hits) or 'geen taak gekoppeld'}.",
        )
    templates = git(
        "diff", "--name-only", f"{commit}..HEAD", "--", "app/admin/templates", "app/web/templates"
    ).split()
    for name in templates:
        report.add(
            section,
            f"Sjabloon gewijzigd: `{name}`; neem de schermafdrukken van dat scherm opnieuw op.",
        )
    if not (renamed or removed or topics or templates):
        report.add(section, "Geen wijzigingen in teksten, hulp of sjablonen.")


def visual_changes(ws: Workspace, report: Report) -> None:
    """Report the screenshots that differ from the previous capture (compare.mjs found them)."""
    section = "Schermafdrukken gewijzigd"
    found = ws.evidence / "diff.json"
    result = json.loads(found.read_text()) if found.exists() else {"compared": False}
    if not result.get("compared"):
        report.add(section, "Geen vorige opname in img/previous/; niets vergeleken.")
        return
    lines = {
        "gone": "`{id}`: niet meer opgenomen.",
        "new": "`{id}`: nieuw opgenomen.",
        "size": "`{id}`: andere afmeting {from} → {to}.",
        "changed": "`{id}`: {share:.1%} van het beeld anders; zie `evidence/diff/{id}.png`.",
    }
    for item in result["results"]:
        report.add(section, lines[item["kind"]].format(**item))
    if not any(item["kind"] in ("size", "changed") for item in result["results"]):
        report.add(section, f"Geen verschillen boven {result['threshold']:.1%}.")


def main() -> int:
    """Run every check and write the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workspace", type=Path, help="the folder of the organisation's manuals")
    parser.add_argument(
        "--since",
        type=commit_hash,
        help="commit of the previous check (default: commit in variables.json)",
    )
    parser.add_argument("--fix", action="store_true", help="write renamed labels into the sources")
    args = parser.parse_args()
    ws = Workspace(args.workspace.resolve())
    guard_outside_checkout(ws.root)
    config = json.loads((ws.root / "variables.json").read_text())
    report = Report()
    sources = {p: p.read_text() for p in sorted(ws.sources.glob("*.md"))}
    shots_file = ws.evidence / "shots.json"
    if not shots_file.exists():
        sys.exit(f"check.py: {shots_file} is missing; run capture.mjs {ws.root} --list first")
    manuals = load_manuals(ws, ws.root / "variables.json")
    variables = frozenset(v.strip() for v in config.values() if isinstance(v, str) and v.strip())
    ctx: dict = {
        "shots": {s["id"] for s in json.loads(shots_file.read_text())},
        "topics": {p.stem for p in (REPO / "docs" / "manual" / "nl").glob("*.md")},
        "codes": {p.value for p in Permission} | {a.key for a in PARTICIPANT_ACTIONS},
        "participant": {a.key for a in PARTICIPANT_ACTIONS},
        "rights": {role.value: {p.value for p in perms} for role, perms in MATRIX.items()},
        "roles": {
            word: [Role(r).value for r in roles]
            for word, roles in {**DEFAULT_ROLES, **config.get("roles", {})}.items()
        },
        "used": set(),
        "tasks": 0,
    }
    catalogue = Catalogue(catalogue_at(None), config.get("extra_screen_texts", []))
    verdicts: dict[str, int] = {}
    for manual in manuals:
        check_tasks(manual, ctx, report)
        if not manual["file"].startswith("00-"):
            check_labels(manual, catalogue, verdicts, report, variables)
    missing = sorted(
        s for s in ctx["used"] if s in ctx["shots"] and not (ws.img / f"{s}.png").exists()
    )
    report.add(
        "Schermafdrukken",
        f"{len(ctx['used'])} gebruikt, {len(missing)} volgen nog: {', '.join(missing) or '–'}.",
    )
    unused = sorted(ctx["shots"] - ctx["used"])
    if unused:
        report.add("Schermafdrukken", f"In de opnamelijst maar niet gebruikt: {', '.join(unused)}.")
    report.add("Koppelingen", f"{ctx['tasks']} taken gekoppeld aan een scherm en een recht.")
    report.add(
        "Schermteksten",
        "Gecontroleerd: " + ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items())) + ".",
    )
    commit = args.since
    if not commit and config.get("commit"):
        try:
            commit = commit_hash(config["commit"])
        except argparse.ArgumentTypeError as error:
            sys.exit(f"check.py: commit in variables.json: {error}")
    if commit:
        since(commit, sources, manuals, args.fix, report, variables)
    visual_changes(ws, report)
    out = ws.evidence / "check.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.markdown())
    print(f"check.py: {report.blocking} blokkerend; rapport in {out}")
    return 1 if report.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
