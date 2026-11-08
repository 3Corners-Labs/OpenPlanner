"""Check an organisation's role manuals against this checkout of the software.

Usage, from the checkout (the app's own modules and babel come from its environment)::

    uv run python .claude/skills/role-manuals/scripts/check.py <workspace> [--since <commit>]
        [--fix]

Writes ``evidence/check.md`` in the workspace and exits with 1 when a blocking finding is left:

1. Task markers (``<!-- task: screen=...; permission=...; shots=... -->``): the screen is a help
   topic of the portal or a participant page, the permission exists, and a role of the manual
   holds it in the authorisation matrix (``app.domain.authorization.MATRIX``).
2. Screenshots: every image and shot id is in the shot list (``evidence/shots.json``, written by
   ``capture.mjs``); which files still have to follow.
3. Labels: every bold label is a text of the software (the ``nl`` catalogue, read as the app reads
   it).
4. Terms the organisation does not want named (``do_not_name``) and, when hidden, the product name.
5. Since a commit: labels the software renamed or removed and where the manuals use them (a
   removed one blocks), help topics and templates that changed. ``--fix`` writes a renamed label
   into the sources, inside bold labels only.
6. Screenshots that differ from the previous capture (``img/previous/``): ``capture.mjs``
   compares them and writes ``evidence/diff.json`` and a picture of each difference in
   ``evidence/diff/``.
"""

import argparse
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

PRODUCT_NAME = "OpenPlanner"
"""The software's own name; hidden in the manuals when ``hide_product_name`` is set."""

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

TASK = re.compile(r"<!--\s*task:\s*(.*?)-->")
BOLD = re.compile(r"\*\*(.+?)\*\*")
BOLD_SPAN = re.compile(r"\*\*((?:(?!\n\s*\n).)+?)\*\*", re.DOTALL)
IMAGE = re.compile(r"!\[[^\]]*\]\(img/([^)\s]+)\.png")


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
    """Stop when ``path`` lies inside a checkout of the software (same rule as workspace.mjs)."""
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


def paragraphs(text: str) -> list[tuple[int, str]]:
    """Return (first line number, text on one line) per paragraph and table row."""
    out: list[tuple[int, str]] = []
    start, lines = 0, []
    for number, line in enumerate([*text.splitlines(), ""], 1):
        if line.strip() and not line.lstrip().startswith(("|", "<!--")):
            if not lines:
                start = number
            lines.append(line.strip())
            continue
        if lines:
            out.append((start, " ".join(lines)))
            lines = []
        if line.lstrip().startswith("|"):
            out.append((number, line))
    return out


def labels_in(line: str) -> list[str]:
    """Return the bold labels of one paragraph; a path (A → B → C) gives its parts."""
    found = []
    for bold in BOLD.findall(line):
        for part in re.split(r"\s*→\s*", bold):
            part = part.strip().rstrip(".")
            if not part or part[0] in "[0123456789" or "{{" in part or part.endswith(":"):
                continue
            if not STRUCTURAL.match(part):
                found.append(part)
    return found


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


def front_matter(text: str) -> dict[str, str]:
    """Return the flat front matter of a source file."""
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    meta = {}
    for line in (match.group(1) if match else "").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip('"')
    return meta


def manual_roles(role: str, config: dict[str, list[str]]) -> list[str] | None:
    """Return the matrix roles of a manual's ``role``, or None for the participants' manual."""
    for word, roles in config.items():
        if word.lower() in role.lower():
            return roles
    return None


def check_markers(path: Path, text: str, ctx: dict, report: Report) -> None:
    """Check the task markers, images and hidden terms of one source."""
    meta = front_matter(text)
    roles = manual_roles(meta.get("role", ""), ctx["roles"])
    for number, line in enumerate(text.splitlines(), 1):
        where = f"{path.name}:{number}"
        for marker in TASK.finditer(line):
            ctx["tasks"] += 1
            attrs = {
                k.strip(): v.strip()
                for k, _, v in (p.partition("=") for p in marker.group(1).split(";"))
                if k.strip()
            }
            screen, permission = attrs.get("screen", ""), attrs.get("permission", "")
            if screen not in ctx["topics"] and screen not in PARTICIPANT_SCREENS:
                report.add(
                    "Koppelingen", f"{where}: scherm `{screen}` bestaat niet.", blocking=True
                )
            if permission not in ctx["codes"]:
                report.add(
                    "Koppelingen", f"{where}: recht `{permission}` bestaat niet.", blocking=True
                )
            elif roles is None and permission not in ctx["participant"]:
                report.add(
                    "Koppelingen",
                    f"{where}: `{permission}` is geen recht van deelnemers.",
                    blocking=True,
                )
            elif roles and not any(permission in ctx["rights"].get(r, set()) for r in roles):
                report.add(
                    "Koppelingen",
                    f"{where}: de rol {meta.get('role')} heeft `{permission}` niet.",
                    blocking=True,
                )
            for shot in filter(None, (s.strip() for s in attrs.get("shots", "").split(","))):
                ctx["used"].add(shot)
                if shot not in ctx["shots"]:
                    report.add(
                        "Koppelingen",
                        f"{where}: schermafdruk `{shot}` staat niet in de opnamelijst.",
                        blocking=True,
                    )
        for shot in IMAGE.findall(line):
            ctx["used"].add(shot)
            if shot not in ctx["shots"]:
                report.add(
                    "Schermafdrukken",
                    f"{where}: `{shot}` staat niet in de opnamelijst.",
                    blocking=True,
                )
        if not line.lstrip().startswith("<!--"):
            low = line.lower()
            for term in ctx["hidden"]:
                if term.lower() in low:
                    report.add("Niet te noemen", f"{where}: `{term}`.", blocking=True)
    if not TASK.search(text) and not path.name.startswith("00-"):
        report.add(
            "Koppelingen", f"{path.name}: geen taakkoppelingen (nodig voor contextgedreven hulp)."
        )


def check_labels(
    path: Path, text: str, catalogue: Catalogue, verdicts: dict, report: Report
) -> None:
    """Check that every bold label of a manual is a text of the software."""
    for number, paragraph in paragraphs(text):
        for label in labels_in(paragraph):
            verdict = catalogue.match(label)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            where = f"{path.name}:{number}"
            if verdict == "onbekend":
                report.add("Schermteksten", f"{where}: **{label}** komt niet voor in de software.")
            elif verdict == "in tekst":
                report.add("Schermteksten (deel van een langere tekst)", f"{where}: **{label}**")


def since(commit: str, sources: dict[Path, str], fix: bool, report: Report) -> None:
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
    for path, text in sources.items():
        for number, paragraph in paragraphs(text):
            for label in labels_in(paragraph):
                core = normalise(label)
                if core in renamed:
                    action = "vervangen" if fix else "gebruik --fix om te vervangen"
                    report.add(
                        section,
                        f"{path.name}:{number}: **{label}** heet nu **{renamed[core]}** "
                        f"({action}).",
                    )
                if core in removed:
                    report.add(
                        section,
                        f"{path.name}:{number}: **{label}** bestaat niet meer in de software.",
                        blocking=True,
                    )
        if fix and renamed:
            changed = rename_labels(text, renamed)
            if changed != text:
                path.write_text(changed)
                sources[path] = changed
    topics = git("diff", "--name-only", f"{commit}..HEAD", "--", "docs/manual/nl").split()
    for topic in topics:
        slug = Path(topic).stem
        hits = [
            f"{p.name} › {m.group(0)[:60]}"
            for p, t in sources.items()
            for m in TASK.finditer(t)
            if f"screen={slug};" in m.group(1).replace(" ", "") + ";"
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
        "--since", help="commit of the previous check (default: commit in variables.json)"
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
    hidden = [t for t in config.get("do_not_name", []) if t]
    if config.get("hide_product_name"):
        hidden.append(PRODUCT_NAME)
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
        "hidden": hidden,
        "used": set(),
        "tasks": 0,
    }
    catalogue = Catalogue(catalogue_at(None), config.get("extra_screen_texts", []))
    verdicts: dict[str, int] = {}
    for path, text in sources.items():
        check_markers(path, text, ctx, report)
        if not path.name.startswith("00-"):
            check_labels(path, text, catalogue, verdicts, report)
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
    commit = args.since or config.get("commit")
    if commit:
        since(commit, sources, args.fix, report)
    visual_changes(ws, report)
    out = ws.evidence / "check.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.markdown())
    print(f"check.py: {report.blocking} blokkerend; rapport in {out}")
    return 1 if report.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
